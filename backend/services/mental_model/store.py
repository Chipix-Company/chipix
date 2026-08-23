from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import MentalModelRevision, Project, ProjectArtifact, User
from services.mental_model.builder import (
    SCHEMA_VERSION,
    build_mental_model,
    build_source_grounded_mental_model,
    summarize_mental_model,
)
from services.mental_model.validator import validate_mental_model
from services.rtl_project_package import (
    archive_for_extracted_rtl_root,
    extract_rtl_archive,
    find_extracted_rtl_root,
    is_rtl_archive_filename,
    is_rtl_source_filename,
    short_rtl_extract_root,
)

logger = logging.getLogger(__name__)


from common.paths import outputs_dir


def _outputs_dir() -> Path:
    return outputs_dir()


def _durable_artifact_candidates(artifact: ProjectArtifact) -> list[Path]:
    """Known durable locations for a project artifact.

    The canonical layout is outputs/orgs/{org}/projects/{project}/artifacts/{type}.
    A few older desktop/agent paths wrote without the orgs segment, or below a
    transient logs directory. These candidates let mental-model preparation
    recover already-created projects instead of failing on stale extracted paths.
    """

    filename = getattr(artifact, "filename", "") or ""
    if not filename:
        return []
    storage_filename = (
        f"v{int(getattr(artifact, 'revision', 0) or 0):04d}_"
        f"{getattr(artifact, 'id', '')}_{filename}"
    )
    outputs = _outputs_dir()
    org_id = str(getattr(artifact, "organization_id", "") or "")
    project_id = str(getattr(artifact, "project_id", "") or "")
    artifact_type = str(getattr(artifact, "artifact_type", "") or "")
    if not org_id or not project_id or not artifact_type:
        return []
    return [
        outputs / "orgs" / org_id / "projects" / project_id / "artifacts" / artifact_type / storage_filename,
        outputs / org_id / "projects" / project_id / "artifacts" / artifact_type / storage_filename,
        outputs / org_id / project_id / "artifacts" / artifact_type / storage_filename,
        outputs / "logs" / org_id / "projects" / project_id / "artifacts" / artifact_type / storage_filename,
    ]


def _find_durable_artifact_file(artifact: ProjectArtifact) -> Path | None:
    for candidate in _durable_artifact_candidates(artifact):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _find_rtl_archive_for_stale_path(
    rtl_artifact: ProjectArtifact,
    stale_path: Path,
) -> Path | None:
    extracted_root = find_extracted_rtl_root(stale_path)
    archive = archive_for_extracted_rtl_root(extracted_root) if extracted_root else None
    if archive and archive.exists() and archive.is_file():
        return archive
    durable = _find_durable_artifact_file(rtl_artifact)
    if durable and is_rtl_archive_filename(durable.name):
        return durable
    return None


def _rtl_extract_dir(rtl_artifact: ProjectArtifact, archive: Path) -> Path:
    return short_rtl_extract_root(
        archive,
        cache_key=(
            f"{getattr(rtl_artifact, 'id', '')}:"
            f"{getattr(rtl_artifact, 'checksum_sha256', '')}:"
            f"{archive.name}"
        ),
    )


def get_latest_mental_model_revision(
    db: Session,
    project_id: str,
) -> MentalModelRevision | None:
    return (
        db.query(MentalModelRevision)
        .filter(MentalModelRevision.project_id == project_id)
        .order_by(MentalModelRevision.revision.desc())
        .first()
    )


def next_mental_model_revision(db: Session, project_id: str) -> int:
    max_revision = (
        db.query(func.max(MentalModelRevision.revision))
        .filter(MentalModelRevision.project_id == project_id)
        .scalar()
    )
    return int(max_revision or 0) + 1


class _MentalModelAIClientAdapter:
    """Adapter expected by builder.build_block_model_with_llm()."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.provider = str(getattr(client, "provider", "") or "")
        self.model = str(getattr(client, "model_alias", "") or "")
        self.calls = 0
        self.successes = 0
        self.failures = 0
        self.last_error = ""
        self.max_tokens = int(
            os.environ.get("CHIPVERIFY_MENTAL_MODEL_LLM_MAX_TOKENS", "8192")
        )

    async def chat(self, system_prompt: str, prompt: str) -> str:
        self.calls += 1
        try:
            response = await self.client.generate(
                prompt,
                system_prompt=system_prompt,
                temperature=0.1,
                max_tokens=self.max_tokens,
            )
            self.successes += 1
            return response
        except Exception as exc:
            self.failures += 1
            self.last_error = str(exc)
            raise


async def _default_ai_client_adapter(
    *,
    require_llm: bool,
) -> tuple[_MentalModelAIClientAdapter | None, dict[str, Any]]:
    """Create the configured project LLM client for mental-model enrichment."""
    try:
        from original_core.core.ai_client import AIClient

        client = AIClient()
        metadata = {
            "provider": getattr(client, "provider", ""),
            "model": getattr(client, "model_alias", ""),
            "status": "configured",
        }
        if not client.is_generation_configured():
            metadata["status"] = "not_configured"
            if require_llm:
                provider = str(getattr(client, "provider", "") or "configured provider")
                model = str(getattr(client, "model_alias", "") or "").strip()
                base_url = str(getattr(client, "base_url", "") or "").strip()
                missing: list[str] = []
                if provider != "gemini" and not base_url:
                    missing.append("base URL")
                if not model:
                    missing.append("model/deployment")
                if getattr(client, "api_key_required", True) and not getattr(client, "api_key", ""):
                    missing.append("API key")
                detail = f" Missing: {', '.join(missing)}." if missing else ""
                raise RuntimeError(
                    f"LLM provider is not configured for mental model enrichment "
                    f"({provider}).{detail}"
                )
            return None, metadata

        if getattr(client, "provider", "") == "local" and not await client.is_runtime_ready():
            metadata["status"] = "runtime_unavailable"
            if require_llm:
                raise RuntimeError(
                    "Local LLM runtime is not reachable for mental model enrichment"
                )
            return None, metadata

        return _MentalModelAIClientAdapter(client), metadata
    except Exception as exc:
        if require_llm:
            raise
        return None, {"status": "unavailable", "error": str(exc)}


async def get_default_ai_client_adapter(
    *,
    require_llm: bool,
) -> tuple[_MentalModelAIClientAdapter | None, dict[str, Any]]:
    """Public helper for staged flows that need the configured LLM client."""
    return await _default_ai_client_adapter(require_llm=require_llm)


def _read_spec_text(spec_artifact: ProjectArtifact) -> str:
    spec_path = Path(spec_artifact.file_path)
    if not spec_path.exists():
        raise FileNotFoundError(f"Artifact file is missing: {spec_artifact.filename}")
    try:
        from services.document_context import spec_text_for_mental_model

        return spec_text_for_mental_model(
            organization_id=str(spec_artifact.organization_id),
            project_id=str(spec_artifact.project_id),
            artifact_id=str(spec_artifact.id),
            file_path=spec_path,
            filename=spec_artifact.filename,
            checksum_sha256=str(spec_artifact.checksum_sha256 or ""),
        )
    except Exception as exc:
        logger.warning(
            "Document-context spec read failed for %s, falling back to full parse: %s",
            spec_artifact.id,
            exc,
        )
    ext = spec_path.suffix.lower()
    if ext == ".pdf":
        from original_core.parsers.pdf_parser import parse_pdf

        return parse_pdf(str(spec_path))
    if ext == ".docx":
        from original_core.parsers.docx_parser import parse_docx

        return parse_docx(str(spec_path))
    return spec_path.read_text(encoding="utf-8", errors="ignore")


def _rtl_root_path(rtl_artifact: ProjectArtifact) -> str:
    rtl_path = Path(rtl_artifact.file_path)

    extracted_root = find_extracted_rtl_root(rtl_path)
    if extracted_root and extracted_root.exists():
        archive = _find_rtl_archive_for_stale_path(rtl_artifact, rtl_path)
        if archive:
            target_root = _rtl_extract_dir(rtl_artifact, archive)
            if target_root != extracted_root or not rtl_path.exists():
                shutil.rmtree(target_root, ignore_errors=True)
                target_root.mkdir(parents=True, exist_ok=True)
                manifest = extract_rtl_archive(archive, target_root)
                manifest_path = target_root / ".chipverify_rtl_manifest.json"
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return str(target_root)
        return str(extracted_root)

    if not rtl_path.exists():
        archive = _find_rtl_archive_for_stale_path(rtl_artifact, rtl_path)
        if archive:
            target_root = _rtl_extract_dir(rtl_artifact, archive)
            shutil.rmtree(target_root, ignore_errors=True)
            target_root.mkdir(parents=True, exist_ok=True)
            manifest = extract_rtl_archive(archive, target_root)
            manifest_path = target_root / ".chipverify_rtl_manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return str(target_root)
        raise FileNotFoundError(f"Artifact file is missing: {rtl_artifact.filename}")

    if rtl_path.is_file() and is_rtl_archive_filename(rtl_path.name):
        extract_dir = _rtl_extract_dir(rtl_artifact, rtl_path)
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        manifest = extract_rtl_archive(rtl_path, extract_dir)
        manifest_path = extract_dir / ".chipverify_rtl_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return str(extract_dir)
    if rtl_path.is_dir():
        return str(rtl_path)

    member_root = find_extracted_rtl_root(rtl_path)
    if member_root and is_rtl_source_filename(rtl_path.name):
        return str(member_root)

    # Single RTL file: parse in an isolated folder so revision siblings are not scanned.
    sandbox = rtl_path.parent / f"_mm_src_{rtl_artifact.id}"
    sandbox.mkdir(parents=True, exist_ok=True)
    target_name = rtl_path.name if is_rtl_source_filename(rtl_path.name) else (rtl_artifact.filename or rtl_path.name)
    target = sandbox / target_name
    if not target.exists() or target.stat().st_mtime < rtl_path.stat().st_mtime:
        shutil.copy2(rtl_path, target)
    return str(sandbox)


def evaluate_mental_model_freshness(
    model: MentalModelRevision | None,
    spec_artifact: ProjectArtifact,
    rtl_artifact: ProjectArtifact,
) -> dict[str, Any]:
    if model is None:
        return {
            "fresh": False,
            "reason": "missing",
            "message": "No mental model revision exists for this project.",
        }

    if model.status != "ready":
        return {
            "fresh": False,
            "reason": "not_ready",
            "message": f"Latest mental model status is {model.status!r}.",
            "model_id": model.id,
            "revision": model.revision,
        }

    if model.schema_version != SCHEMA_VERSION:
        return {
            "fresh": False,
            "reason": "schema_changed",
            "message": (
                f"Mental model schema {model.schema_version!r} does not match "
                f"runtime schema {SCHEMA_VERSION!r}."
            ),
            "model_id": model.id,
            "revision": model.revision,
        }

    if model.source_spec_artifact_id != spec_artifact.id:
        return {
            "fresh": False,
            "reason": "spec_artifact_changed",
            "message": "Active spec artifact changed since the latest mental model.",
            "model_id": model.id,
            "revision": model.revision,
        }

    if model.source_rtl_artifact_id != rtl_artifact.id:
        return {
            "fresh": False,
            "reason": "rtl_artifact_changed",
            "message": "Active RTL artifact changed since the latest mental model.",
            "model_id": model.id,
            "revision": model.revision,
        }

    content = _load_model_content(model)
    if content is None:
        return {
            "fresh": False,
            "reason": "content_unreadable",
            "message": "Latest mental model content JSON could not be parsed.",
            "model_id": model.id,
            "revision": model.revision,
        }

    spec_checksum = _source_checksum(content, "spec")
    rtl_checksum = _source_checksum(content, "rtl")
    current_spec_checksum = _artifact_checksum(spec_artifact)
    current_rtl_checksum = _artifact_checksum(rtl_artifact)
    if spec_checksum and current_spec_checksum and spec_checksum != current_spec_checksum:
        return {
            "fresh": False,
            "reason": "spec_checksum_changed",
            "message": "Spec artifact content changed after the latest mental model.",
            "model_id": model.id,
            "revision": model.revision,
        }

    if rtl_checksum and current_rtl_checksum and rtl_checksum != current_rtl_checksum:
        rtl_delta = _rtl_source_checksum_delta(content, rtl_artifact)
        delta_rebuild_plan = _rtl_delta_rebuild_plan(content, rtl_delta)
        return {
            "fresh": False,
            "reason": "rtl_checksum_changed",
            "message": "RTL artifact content changed after the latest mental model.",
            "model_id": model.id,
            "revision": model.revision,
            "changed_files": rtl_delta.get("changed_files", []),
            "added_files": rtl_delta.get("added_files", []),
            "removed_files": rtl_delta.get("removed_files", []),
            "delta_rebuild_possible": bool(rtl_delta.get("changed_files") or rtl_delta.get("added_files") or rtl_delta.get("removed_files")),
            "delta_rebuild_plan": delta_rebuild_plan,
        }

    return {
        "fresh": True,
        "reason": "current",
        "message": "Mental model matches the active spec and RTL artifacts.",
        "model_id": model.id,
        "revision": model.revision,
    }


def create_mental_model_revision(
    db: Session,
    *,
    project: Project,
    user: User,
    spec_artifact: ProjectArtifact,
    rtl_artifact: ProjectArtifact,
) -> tuple[MentalModelRevision, dict[str, Any], str]:
    _require_artifact_file(spec_artifact)
    _require_artifact_file(rtl_artifact)

    revision = next_mental_model_revision(db, project.id)
    content = build_source_grounded_mental_model(
        project_id=project.id,
        spec_artifact=spec_artifact,
        rtl_artifact=rtl_artifact,
        revision=revision,
    )
    content.setdefault("kind", "chipstack_style_mental_model")
    if "verification_intent" not in content and "verification" in content:
        content["verification_intent"] = content["verification"]
    if content.get("status") == "error":
        raise RuntimeError(
            content.get("message") or "Mental model build failed."
        )
    summary = summarize_mental_model(content)
    record = MentalModelRevision(
        id=str(uuid.uuid4()),
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        revision=revision,
        status="ready",
        schema_version=str(content.get("schema_version") or SCHEMA_VERSION),
        source_spec_artifact_id=spec_artifact.id,
        source_rtl_artifact_id=rtl_artifact.id,
        summary_text=summary,
        content_json=json.dumps(content, indent=2),
    )
    db.add(record)
    db.flush()
    return record, content, summary


async def create_llm_enriched_mental_model_revision(
    db: Session,
    *,
    project: Project,
    user: User,
    spec_artifact: ProjectArtifact,
    rtl_artifact: ProjectArtifact,
    target_module: str | None = None,
    require_llm: bool = True,
) -> tuple[MentalModelRevision, dict[str, Any], str]:
    """Build and persist a parser-grounded, LLM-enriched mental model revision."""
    _require_artifact_file(spec_artifact)
    _require_artifact_file(rtl_artifact)

    revision = next_mental_model_revision(db, project.id)
    spec_text = _read_spec_text(spec_artifact)
    rtl_root = _rtl_root_path(rtl_artifact)
    ai_client, llm_metadata = await _default_ai_client_adapter(
        require_llm=require_llm,
    )

    model = await build_mental_model(
        root_path=rtl_root,
        project_id=project.id,
        spec_text=spec_text,
        target_module=target_module or None,
        ai_client=ai_client,
    )
    model.revision = revision
    model.schema_version = SCHEMA_VERSION

    llm_status = str(getattr(model, "_llm_status", "not_requested"))
    module_count = len(getattr(model, "block_models", {}) or {})
    if require_llm and ai_client and ai_client.successes == 0:
        if module_count == 0:
            hint = (
                f"Check that `{rtl_artifact.filename}` contains valid `module`/`endmodule` "
                "definitions (SystemVerilog/Verilog source)."
            )
            if (rtl_artifact.filename or "").lower().endswith(".txt"):
                hint += (
                    " Files ending in `.txt` are only scanned as RTL when they contain "
                    "`module`/`endmodule`; otherwise re-upload as `.sv` or `.v`."
                )
            raise RuntimeError(
                "Could not parse any RTL modules from the active RTL artifact. " + hint
            )
        if ai_client.calls > 0:
            detail = ai_client.last_error or "unknown error"
            raise RuntimeError(
                f"LLM mental model enrichment failed after {ai_client.calls} call(s): {detail}"
            )
        raise RuntimeError(
            "LLM mental model enrichment was not called — no analyzable top module was resolved. "
            "Try re-uploading RTL or specify target_module in the build request."
        )
    acceptable_llm_statuses = {
        "parsed",
        "multi_block_parsed",
        "multi_block_partial",
        "structure_with_llm_attempt",
    }
    if require_llm and llm_status not in acceptable_llm_statuses:
        if module_count > 0 and llm_status in {"llm_error", "structure_with_llm_attempt"}:
            logger.warning(
                "LLM enrichment incomplete (status=%s); persisting parser-grounded mental model",
                llm_status,
            )
        else:
            raise RuntimeError(
                f"LLM mental model enrichment did not produce valid structured output: {llm_status}"
            )

    validation = validate_mental_model(model)
    if not validation.valid:
        raise ValueError(
            "Mental model validation failed: " + "; ".join(validation.errors)
        )

    content = model.to_dict()
    content.setdefault("kind", "chipstack_style_mental_model")
    if "verification_intent" not in content and "verification" in content:
        content["verification_intent"] = content["verification"]
    content["sources"] = {
        "spec": {
            "artifact_id": spec_artifact.id,
            "checksum_sha256": spec_artifact.checksum_sha256,
        },
        "rtl": {
            "artifact_id": rtl_artifact.id,
            "checksum_sha256": rtl_artifact.checksum_sha256,
        },
    }
    llm_structured = llm_status in {"parsed", "multi_block_parsed"}
    content["build_mode"] = (
        "llm_enriched"
        if llm_structured
        else "llm_attempted_source_grounded"
        if ai_client
        else "source_grounded"
    )
    content["source_grounded"] = True
    content["llm_provider"] = llm_metadata.get("provider")
    content["llm_model"] = llm_metadata.get("model")
    content["llm_status"] = llm_status
    content["llm_structured"] = llm_structured
    content["llm_block_count"] = int(getattr(model, "_llm_block_count", 0) or 0)
    content["llm_analyzed_modules"] = list(getattr(model, "_llm_analyzed_modules", []) or [])
    content["llm_block_statuses"] = dict(getattr(model, "_llm_block_statuses", {}) or {})
    content["llm_integration_status"] = str(
        getattr(model, "_llm_integration_status", "not_requested") or "not_requested"
    )
    content["llm_integration_mode"] = str(
        getattr(model, "_llm_integration_mode", "not_requested") or "not_requested"
    )
    content["llm_integration_agent_status"] = str(
        getattr(model, "_llm_integration_agent_status", "not_requested") or "not_requested"
    )
    content["llm_integration_agent_steps"] = int(
        getattr(model, "_llm_integration_agent_steps", 0) or 0
    )
    if ai_client and not llm_structured:
        content["llm_warning"] = (
            "The LLM was called but did not return valid structured JSON; "
            "this revision uses parser-grounded structure plus extracted spec facts."
        )
    content["llm_calls"] = getattr(ai_client, "successes", 0) if ai_client else 0
    content["validation"] = asdict(validation)

    summary = summarize_mental_model(content)
    record = MentalModelRevision(
        id=str(uuid.uuid4()),
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        revision=revision,
        status="ready",
        schema_version=str(content.get("schema_version") or SCHEMA_VERSION),
        source_spec_artifact_id=spec_artifact.id,
        source_rtl_artifact_id=rtl_artifact.id,
        summary_text=summary,
        content_json=json.dumps(content, indent=2),
    )
    db.add(record)
    db.flush()
    return record, content, summary


def ensure_fresh_mental_model_revision(
    db: Session,
    *,
    project: Project,
    user: User,
    spec_artifact: ProjectArtifact,
    rtl_artifact: ProjectArtifact,
) -> tuple[MentalModelRevision, bool, dict[str, Any], str]:
    latest_model = get_latest_mental_model_revision(db, project.id)
    freshness = evaluate_mental_model_freshness(
        latest_model,
        spec_artifact,
        rtl_artifact,
    )
    if freshness["fresh"] and latest_model is not None:
        return latest_model, False, freshness, latest_model.summary_text or ""

    record, _content, summary = create_mental_model_revision(
        db,
        project=project,
        user=user,
        spec_artifact=spec_artifact,
        rtl_artifact=rtl_artifact,
    )
    return record, True, freshness, summary


def _load_model_content(model: MentalModelRevision) -> dict[str, Any] | None:
    try:
        data = json.loads(model.content_json or "{}")
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _source_checksum(content: dict[str, Any], source_kind: str) -> str | None:
    sources = content.get("sources") or {}
    source = sources.get(source_kind) or {}
    checksum = source.get("checksum_sha256")
    return str(checksum) if checksum else None


def _artifact_checksum(artifact: ProjectArtifact) -> str | None:
    checksum = getattr(artifact, "checksum_sha256", None)
    if checksum:
        return str(checksum)
    path_value = getattr(artifact, "file_path", None)
    if not path_value:
        return None
    path = Path(path_value)
    try:
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
    return None


def _rtl_source_checksum_delta(
    content: dict[str, Any],
    rtl_artifact: ProjectArtifact,
) -> dict[str, list[str]]:
    """Compare saved per-file RTL checksums with the current artifact contents."""
    saved = content.get("source_checksums") or {}
    if not isinstance(saved, dict) or not saved:
        return {"changed_files": [], "added_files": [], "removed_files": []}
    try:
        root = Path(_rtl_root_path(rtl_artifact))
    except Exception:
        return {"changed_files": [], "added_files": [], "removed_files": []}

    rtl_suffixes = {".v", ".sv", ".svh", ".vh"}
    current: dict[str, str] = {}
    try:
        files = [root] if root.is_file() else list(root.rglob("*"))
        for path in files:
            if not path.is_file() or path.suffix.lower() not in rtl_suffixes:
                continue
            rel = path.name if root.is_file() else str(path.relative_to(root)).replace("\\", "/")
            try:
                current[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
    except OSError:
        return {"changed_files": [], "added_files": [], "removed_files": []}

    saved_keys = {str(key).replace("\\", "/") for key in saved.keys()}
    current_keys = set(current.keys())
    changed = sorted(
        key
        for key in saved_keys & current_keys
        if str(saved.get(key) or saved.get(key.replace("/", "\\")) or "") != current[key]
    )
    added = sorted(current_keys - saved_keys)
    removed = sorted(saved_keys - current_keys)
    return {
        "changed_files": changed,
        "added_files": added,
        "removed_files": removed,
    }


def _rtl_delta_rebuild_plan(
    content: dict[str, Any],
    rtl_delta: dict[str, list[str]],
) -> dict[str, Any]:
    """Create a conservative affected-module plan for future delta rebuilds."""
    changed_files = set(rtl_delta.get("changed_files") or [])
    added_files = set(rtl_delta.get("added_files") or [])
    removed_files = set(rtl_delta.get("removed_files") or [])
    touched_files = changed_files | added_files | removed_files
    if not touched_files:
        return {
            "mode": "none",
            "affected_modules": [],
            "reuse_candidate_modules": [],
            "reason": "No per-file RTL delta could be computed.",
        }

    block_models = content.get("block_models") if isinstance(content.get("block_models"), dict) else {}
    hierarchy = {}
    design = content.get("design") if isinstance(content.get("design"), dict) else {}
    if isinstance(design.get("hierarchy_tree"), dict):
        hierarchy = design.get("hierarchy_tree") or {}

    module_files: dict[str, set[str]] = {}
    for module_name, block in block_models.items():
        files = _collect_block_source_files(block)
        if files:
            module_files[str(module_name)] = files

    directly_affected = sorted(
        module
        for module, files in module_files.items()
        if files & touched_files
    )
    affected = set(directly_affected)
    changed = True
    while changed:
        changed = False
        for parent, children in hierarchy.items():
            if parent in affected:
                continue
            if any(str(child) in affected for child in (children or [])):
                affected.add(str(parent))
                changed = True

    all_modules = set(str(module) for module in block_models.keys())
    reuse_candidates = sorted(all_modules - affected)
    mode = "incremental_candidate" if affected and reuse_candidates else "full_rebuild_recommended"
    if added_files or removed_files:
        mode = "full_rebuild_recommended"

    return {
        "mode": mode,
        "directly_affected_modules": directly_affected,
        "affected_modules": sorted(affected),
        "reuse_candidate_modules": reuse_candidates,
        "touched_files": sorted(touched_files),
        "reason": (
            "Added/removed files can change hierarchy; full rebuild is safer."
            if added_files or removed_files
            else "Changed files map to known block models; unchanged blocks are reuse candidates."
        ),
    }


def _collect_block_source_files(block: Any) -> set[str]:
    files: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            source_ref = value.get("source_ref")
            if isinstance(source_ref, dict):
                file_name = str(source_ref.get("file") or "").replace("\\", "/")
                if file_name:
                    files.add(file_name)
                    files.add(Path(file_name).name)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(block)
    return files


def _require_artifact_file(artifact: ProjectArtifact) -> None:
    artifact_path = Path(artifact.file_path)
    if artifact_path.exists():
        return
    if getattr(artifact, "artifact_type", "") == "rtl":
        extracted_root = find_extracted_rtl_root(artifact_path)
        if extracted_root and extracted_root.exists():
            return
        archive = _find_rtl_archive_for_stale_path(artifact, artifact_path)
        if archive and archive.exists():
            return
    durable = _find_durable_artifact_file(artifact)
    if durable:
        return
    raise FileNotFoundError(f"Artifact file is missing: {artifact.filename}")
