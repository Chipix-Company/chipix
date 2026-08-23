from __future__ import annotations

import os
from pathlib import Path

from common.paths import outputs_dir as resolve_outputs_dir

SV_EXTENSIONS = {".sv", ".svh", ".v", ".vh"}

DEFAULT_SVLS_TOML = """[verilog]
include_paths = ["rtl", "generated", "spec"]

[option]
linter = true
"""

DISABLED_DEFAULT_SVLS_TOML = """[verilog]
include_paths = ["rtl", "generated", "spec"]

[option]
linter = false
"""

DEFAULT_SVLINT_TOML = """[textrules]

[syntaxrules]
"""

DEFAULT_SVLS_TOML_NAME = ".svls.toml"
DEFAULT_SVLINT_TOML_NAME = ".svlint.toml"


def is_rtl_filename(filename: str) -> bool:
    return Path(filename).suffix.lower() in SV_EXTENSIONS


def should_lint_artifact(filename: str, artifact_type: str | None = None) -> bool:
    if not is_rtl_filename(filename):
        return False
    if artifact_type is None:
        return True
    return artifact_type in {"rtl", "generated"}


def project_artifacts_root(
    *,
    organization_id: str,
    project_id: str,
    outputs_dir: Path | None = None,
) -> Path:
    root = outputs_dir if outputs_dir is not None else resolve_outputs_dir()
    return root / "orgs" / organization_id / "projects" / project_id / "artifacts"


def _remove_stale_svlint_config(project_root: Path) -> None:
    """Replace legacy generated configs while preserving user-owned rules."""
    svlint_path = project_root / DEFAULT_SVLINT_TOML_NAME
    if not svlint_path.exists():
        return
    try:
        text = svlint_path.read_text(encoding="utf-8")
    except OSError:
        return
    legacy_markers = ("wire_reg", "legacy_always")
    if any(marker in text for marker in legacy_markers):
        svlint_path.write_text(DEFAULT_SVLINT_TOML, encoding="utf-8")


def ensure_project_lint_config(project_root: Path) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    _remove_stale_svlint_config(project_root)

    svls_path = project_root / DEFAULT_SVLS_TOML_NAME
    if svls_path.exists():
        try:
            current = svls_path.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current.strip() == DISABLED_DEFAULT_SVLS_TOML.strip():
            svls_path.write_text(DEFAULT_SVLS_TOML, encoding="utf-8")
    else:
        svls_path.write_text(DEFAULT_SVLS_TOML, encoding="utf-8")

    # An absent svlint config enables every rule, including conflicting style
    # rules. Empty rule sections retain parser diagnostics without style noise.
    svlint_path = project_root / DEFAULT_SVLINT_TOML_NAME
    if not svlint_path.exists():
        svlint_path.write_text(DEFAULT_SVLINT_TOML, encoding="utf-8")


def safe_project_relative_path(raw_path: str, fallback_name: str = "untitled.sv") -> Path:
    """Return a traversal-safe project-relative source path."""
    normalized = str(raw_path or fallback_name).strip().replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute() or candidate.drive:
        candidate = Path(candidate.name)

    parts = [part for part in candidate.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return Path(fallback_name)
    return Path(*parts)


def infer_project_root_from_file(file_path: Path) -> Path:
    resolved = file_path.resolve()
    parts = resolved.parts
    if "artifacts" in parts:
        idx = parts.index("artifacts")
        return Path(*parts[: idx + 1])
    return resolved.parent


def lsp_language_id(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".v") or lower.endswith(".vh"):
        return "verilog"
    return "systemverilog"
