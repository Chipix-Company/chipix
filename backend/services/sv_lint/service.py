from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from services.sv_lint.config import (
    ensure_project_lint_config,
    infer_project_root_from_file,
    lsp_language_id,
    safe_project_relative_path,
)
from services.sv_lint.lsp_client import SvlsLspClient
from services.sv_lint.schemas import SvDiagnostic, SvLintResult
from services.sv_lint.toolchain import resolve_svls_binary, svls_available

logger = logging.getLogger(__name__)


def _require_svls() -> bool:
    return (os.environ.get("CHIPVERIFY_REQUIRE_SVLS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


class SvlsLintService:
    def lint_file(self, file_path: str | Path) -> SvLintResult:
        path = Path(file_path)
        if not path.exists():
            return SvLintResult(
                passed=False,
                tool_available=svls_available(),
                errors=[f"File not found: {path}"],
            )
        content = path.read_text(encoding="utf-8", errors="replace")
        project_root = infer_project_root_from_file(path)
        return self.lint_source(
            filepath=path.name,
            content=content,
            project_root=project_root,
            absolute_path=path,
        )

    def lint_source(
        self,
        *,
        filepath: str,
        content: str,
        project_root: Path,
        absolute_path: Path | None = None,
        version: int = 1,
    ) -> SvLintResult:
        binary = resolve_svls_binary()
        if not binary:
            if _require_svls():
                return SvLintResult(
                    passed=False,
                    tool_available=False,
                    errors=["svls is not installed. Install via `cargo install svls` or bundle runtime/bin/svls.exe."],
                )
            return SvLintResult(
                passed=True,
                tool_available=False,
                skipped=True,
                skip_reason="svls binary not found",
                warnings=["svls is not installed; lint skipped."],
            )

        ensure_project_lint_config(project_root)
        relative_path = safe_project_relative_path(filepath)
        target_path = absolute_path or (project_root / relative_path)
        if absolute_path is None:
            target_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            client = SvlsLspClient(binary)
            diagnostics = client.lint_document(
                file_path=target_path,
                content=content,
                project_root=project_root,
                language_id=lsp_language_id(filepath),
                version=version,
            )
        except Exception as exc:
            logger.exception("svls lint failed for %s", target_path)
            return SvLintResult(
                passed=False,
                tool_available=True,
                errors=[f"svls lint failed: {exc}"],
            )

        errors = [d.message for d in diagnostics if d.is_error]
        warnings = [d.message for d in diagnostics if not d.is_error]
        passed = not any(d.is_error for d in diagnostics)
        return SvLintResult(
            passed=passed,
            tool_available=True,
            diagnostics=diagnostics,
            errors=errors,
            warnings=warnings,
        )


@lru_cache(maxsize=1)
def get_svls_lint_service() -> SvlsLintService:
    return SvlsLintService()
