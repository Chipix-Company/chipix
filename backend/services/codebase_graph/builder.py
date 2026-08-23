"""Parallel workers: AST, Slang, spec, KB → merged graphify graph."""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from services.codebase_graph.events import emit_project_event_sync
from services.codebase_graph.schemas import BuildStatus, WorkerResult
from services.codebase_graph.store import append_build_log, write_status

logger = logging.getLogger(__name__)

RTL_SUFFIXES = {".v", ".sv", ".svh", ".vh", ".vhd", ".vhdl"}
SPEC_SUFFIXES = {".md", ".txt", ".rst", ".yaml", ".yml"}


def _merge_extractions(parts: list[WorkerResult]) -> dict[str, Any]:
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_node_ids: set[str] = set()
    for part in parts:
        for node in part.nodes:
            nid = str(node.get("id") or "")
            if nid and nid not in seen_node_ids:
                seen_node_ids.add(nid)
                nodes.append(node)
        edges.extend(part.edges)
    return {"nodes": nodes, "edges": edges}


def _worker_ast(rtl_root: Path, cache_root: Path) -> WorkerResult:
    try:
        from graphify.extract import collect_files, extract

        files = [
            p
            for p in collect_files(rtl_root, root=rtl_root)
            if p.suffix.lower() in RTL_SUFFIXES
        ]
        if not files:
            return WorkerResult(name="ast", nodes=[], edges=[])
        result = extract(files, cache_root=cache_root, parallel=len(files) >= 20)
        return WorkerResult(
            name="ast",
            nodes=list(result.get("nodes") or []),
            edges=list(result.get("edges") or []),
        )
    except Exception as exc:
        logger.warning("AST worker failed: %s", exc, exc_info=True)
        return WorkerResult(name="ast", error=str(exc))


def _worker_slang(rtl_root: Path, rtl_files: list[Path]) -> WorkerResult:
    try:
        from services.mental_model.slang_analyzer import (
            SlangAnalysisError,
            SlangStructuralAnalyzer,
        )

        analyzer = SlangStructuralAnalyzer()
        rel_paths = [
            str(p.relative_to(rtl_root)).replace("\\", "/")
            for p in rtl_files
            if p.exists()
        ]
        if not rel_paths:
            return WorkerResult(name="slang", nodes=[], edges=[])

        result = analyzer.analyze_project(str(rtl_root), rel_paths)
        nodes: list[dict] = []
        edges: list[dict] = []

        for mod_name, info in (result.module_index or {}).items():
            nid = f"slang:module:{mod_name.lower()}"
            nodes.append(
                {
                    "id": nid,
                    "label": mod_name,
                    "file_type": "code",
                    "source_file": info.get("file") or "",
                    "confidence_score": 1.0,
                }
            )
            for port in info.get("ports") or []:
                if isinstance(port, dict):
                    pname = port.get("name") or port.get("port_name")
                    direction = port.get("direction") or ""
                else:
                    pname = str(port)
                    direction = ""
                if not pname:
                    continue
                pid = f"slang:port:{mod_name.lower()}:{pname.lower()}"
                nodes.append(
                    {
                        "id": pid,
                        "label": f"{pname} ({direction})".strip(),
                        "file_type": "concept",
                        "confidence_score": 1.0,
                    }
                )
                edges.append(
                    {
                        "source": nid,
                        "target": pid,
                        "relation": "has_port",
                        "confidence": "EXTRACTED",
                        "confidence_score": 1.0,
                        "weight": 1.0,
                    }
                )
            for inst in info.get("instantiations") or info.get("instances") or []:
                if isinstance(inst, dict):
                    iname = inst.get("module") or inst.get("instance") or inst.get("name")
                else:
                    iname = str(inst)
                if not iname:
                    continue
                tgt = f"slang:module:{iname.lower()}"
                nodes.append(
                    {
                        "id": tgt,
                        "label": iname,
                        "file_type": "code",
                        "confidence_score": 0.9,
                    }
                )
                edges.append(
                    {
                        "source": nid,
                        "target": tgt,
                        "relation": "instantiates",
                        "confidence": "INFERRED",
                        "confidence_score": 0.9,
                        "weight": 1.0,
                    }
                )
        return WorkerResult(name="slang", nodes=nodes, edges=edges)
    except SlangAnalysisError as exc:
        logger.info("Slang worker skipped: %s", exc)
        return WorkerResult(name="slang", error=str(exc))
    except Exception as exc:
        logger.warning("Slang worker failed: %s", exc, exc_info=True)
        return WorkerResult(name="slang", error=str(exc))


def _worker_spec(spec_paths: list[Path], module_names: set[str]) -> WorkerResult:
    nodes: list[dict] = []
    edges: list[dict] = []
    heading_re = re.compile(r"^#{1,4}\s+(.+)$", re.MULTILINE)

    for path in spec_paths:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        rel = path.name
        file_nid = f"spec:file:{rel.lower()}"
        nodes.append(
            {
                "id": file_nid,
                "label": rel,
                "file_type": "document",
                "source_file": rel,
                "confidence_score": 1.0,
            }
        )

        for match in heading_re.finditer(text):
            title = match.group(1).strip()
            if len(title) < 4:
                continue
            req_id = f"spec:req:{rel.lower()}:{title[:48].lower()}"
            req_id = re.sub(r"[^\w:]+", "_", req_id)
            nodes.append(
                {
                    "id": req_id,
                    "label": title[:120],
                    "file_type": "document",
                    "source_file": rel,
                    "confidence_score": 0.85,
                }
            )
            edges.append(
                {
                    "source": file_nid,
                    "target": req_id,
                    "relation": "defines",
                    "confidence": "EXTRACTED",
                    "confidence_score": 0.85,
                    "weight": 1.0,
                }
            )
            section = text[match.start() : match.start() + 2000].lower()
            for mod in module_names:
                if mod.lower() in section:
                    mod_nid = f"slang:module:{mod.lower()}"
                    edges.append(
                        {
                            "source": req_id,
                            "target": mod_nid,
                            "relation": "requires",
                            "confidence": "INFERRED",
                            "confidence_score": 0.7,
                            "weight": 1.0,
                        }
                    )
    return WorkerResult(name="spec", nodes=nodes, edges=edges)


def _worker_kb(module_names: set[str]) -> WorkerResult:
    nodes: list[dict] = []
    edges: list[dict] = []
    try:
        from services.mental_model.knowledge_base import SIGNAL_RULES, ALL_TEMPLATES

        for rule in SIGNAL_RULES[:24]:
            rid = f"kb:signal:{getattr(rule, 'id', str(rule))}"
            label = getattr(rule, "title", str(rule))[:80]
            nodes.append(
                {
                    "id": rid,
                    "label": label,
                    "file_type": "concept",
                    "confidence_score": 0.8,
                }
            )
            for pattern in getattr(rule, "trigger_patterns", []) or []:
                for mod in module_names:
                    if pattern.lower() in mod.lower():
                        edges.append(
                            {
                                "source": rid,
                                "target": f"slang:module:{mod.lower()}",
                                "relation": "warns_about",
                                "confidence": "INFERRED",
                                "confidence_score": 0.6,
                                "weight": 1.0,
                            }
                        )

        for template in ALL_TEMPLATES[:12]:
            tid = f"kb:corner:{getattr(template, 'id', str(template))}"
            label = getattr(template, "title", getattr(template, "name", "corner case"))[:80]
            nodes.append(
                {
                    "id": tid,
                    "label": label,
                    "file_type": "concept",
                    "confidence_score": 0.75,
                }
            )
    except Exception as exc:
        logger.debug("KB worker partial: %s", exc)
        return WorkerResult(name="kb", error=str(exc))

    return WorkerResult(name="kb", nodes=nodes, edges=edges)


def _collect_rtl_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in RTL_SUFFIXES:
            files.append(path)
    return files


def _collect_spec_files(spec_path: Optional[Path], corpus_root: Path) -> list[Path]:
    paths: list[Path] = []
    if spec_path and spec_path.exists():
        if spec_path.is_file():
            paths.append(spec_path)
        else:
            for p in spec_path.rglob("*"):
                if p.is_file() and p.suffix.lower() in SPEC_SUFFIXES:
                    paths.append(p)
    return paths


def _module_names_from_ast(extraction: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for node in extraction.get("nodes") or []:
        label = str(node.get("label") or "")
        if label and node.get("file_type") == "code":
            names.add(label.split("(")[0].strip())
    return names


def graphify_available() -> bool:
    """True when the optional `graphify` package is importable.

    `graphify` is an optional research dependency. It is not always
    bundled — e.g. when it was absent from the build context. The codebase knowledge
    graph is a best-effort enhancement, so when graphify is missing we skip the build
    cleanly instead of letting a ModuleNotFoundError escape a background thread.
    """
    import importlib.util

    return importlib.util.find_spec("graphify") is not None


def build_codebase_graph(
    *,
    project_id: str,
    organization_id: str,
    graph_dir: Path,
    rtl_root: Path,
    spec_path: Optional[Path] = None,
    trigger: str = "upload",
) -> dict[str, Any]:
    """Run parallel workers and write graphify artifacts."""

    if not graphify_available():
        logger.info(
            "Codebase graph skipped for project %s: optional 'graphify' package is not installed",
            project_id,
        )
        graph_dir.mkdir(parents=True, exist_ok=True)
        status = {
            "status": BuildStatus.PENDING.value,
            "trigger": trigger,
            "skipped": True,
            "message": "codebase_graph_unavailable",
        }
        write_status(graph_dir, status)
        return status

    graph_dir.mkdir(parents=True, exist_ok=True)
    cache_root = graph_dir

    def _progress(msg: str, *, event_type: str = "codebase_graph_build_progress") -> None:
        write_status(
            graph_dir,
            {
                "status": BuildStatus.BUILDING.value,
                "trigger": trigger,
                "progress": msg,
            },
        )
        append_build_log(graph_dir, {"event": event_type, "message": msg})
        emit_project_event_sync(project_id, {"type": event_type, "progress": msg})

    write_status(
        graph_dir,
        {"status": BuildStatus.BUILDING.value, "trigger": trigger, "progress": "starting"},
    )
    emit_project_event_sync(
        project_id,
        {"type": "codebase_graph_build_started", "trigger": trigger},
    )

    try:
        rtl_files = _collect_rtl_files(rtl_root)
        _progress(f"discovered {len(rtl_files)} RTL files")

        worker_results: list[WorkerResult] = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_worker_ast, rtl_root, cache_root): "ast",
                pool.submit(_worker_slang, rtl_root, rtl_files): "slang",
            }
            ast_result: Optional[WorkerResult] = None
            for fut in as_completed(futures):
                name = futures[fut]
                result = fut.result()
                worker_results.append(result)
                _progress(f"worker {name} done ({len(result.nodes)} nodes)")

            ast_result = next((r for r in worker_results if r.name == "ast"), None)
            module_names = _module_names_from_ast(
                {"nodes": ast_result.nodes if ast_result else []}
            )
            for slang_result in worker_results:
                if slang_result.name != "slang":
                    continue
                for mod in slang_result.nodes:
                    if not isinstance(mod, dict):
                        continue
                    label = str(mod.get("label") or "")
                    if label:
                        module_names.add(label.split("(")[0].strip())

            spec_paths = _collect_spec_files(spec_path, rtl_root)
            kb_fut = pool.submit(_worker_kb, module_names)
            spec_fut = pool.submit(_worker_spec, spec_paths, module_names)
            for fut in as_completed([spec_fut, kb_fut]):
                result = fut.result()
                worker_results.append(result)
                _progress(f"worker {result.name} done ({len(result.nodes)} nodes)")

        merged = _merge_extractions(worker_results)
        _progress(f"merged {len(merged['nodes'])} nodes, {len(merged['edges'])} edges")

        from graphify.build import build_from_json
        from graphify.cluster import cluster, score_all
        from graphify.analyze import god_nodes, suggest_questions, surprising_connections
        from graphify.report import generate
        from graphify.export import to_json, to_html
        from graphify.detect import detect

        root_str = str(rtl_root)
        G = build_from_json(merged, root=root_str)
        communities = cluster(G)
        cohesion = score_all(G, communities)
        community_labels = {cid: f"Community {cid}" for cid in communities}
        gods = god_nodes(G)
        surprises = surprising_connections(G, communities)
        questions = suggest_questions(G, communities, community_labels)
        detection = detect(rtl_root)

        report_md = generate(
            G,
            communities,
            cohesion,
            community_labels,
            gods,
            surprises,
            detection,
            token_cost={"estimated_tokens": 0},
            root=root_str,
            suggested_questions=questions,
        )
        (graph_dir / "GRAPH_REPORT.md").write_text(report_md, encoding="utf-8")
        to_json(G, communities, str(graph_dir / "graph.json"), force=True)
        to_html(
            G,
            communities,
            str(graph_dir / "graph.html"),
            community_labels=community_labels,
        )

        built_at = datetime.now(timezone.utc).isoformat()
        write_status(
            graph_dir,
            {
                "status": BuildStatus.READY.value,
                "trigger": trigger,
                "progress": "complete",
                "built_at": built_at,
                "node_count": G.number_of_nodes(),
                "edge_count": G.number_of_edges(),
            },
        )
        emit_project_event_sync(
            project_id,
            {
                "type": "codebase_graph_build_completed",
                "trigger": trigger,
                "node_count": G.number_of_nodes(),
                "edge_count": G.number_of_edges(),
                "built_at": built_at,
            },
        )
        return {
            "status": BuildStatus.READY.value,
            "node_count": G.number_of_nodes(),
            "edge_count": G.number_of_edges(),
            "built_at": built_at,
        }
    except Exception as exc:
        logger.exception("Graph build failed for project %s", project_id)
        write_status(
            graph_dir,
            {
                "status": BuildStatus.ERROR.value,
                "trigger": trigger,
                "error": str(exc),
            },
        )
        emit_project_event_sync(
            project_id,
            {"type": "codebase_graph_build_error", "error": str(exc)},
        )
        return {"status": BuildStatus.ERROR.value, "error": str(exc)}
