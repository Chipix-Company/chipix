from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.sv_lint.config import should_lint_artifact
from services.sv_lint.schemas import SvLintResult
from services.sv_lint.service import get_svls_lint_service

LINT_FAILED_PREFIX = "LINT_FAILED:"


def run_post_write_sv_lint(
    file_path: str | Path,
    *,
    filename: str | None = None,
    artifact_type: str | None = None,
) -> SvLintResult | None:
    path = Path(file_path)
    name = filename or path.name
    if not should_lint_artifact(name, artifact_type):
        return None
    return get_svls_lint_service().lint_file(path)


def lint_failure_error_payload(result: SvLintResult, *, filename: str) -> str:
    payload = {
        "message": f"SystemVerilog lint failed for `{filename}`. Fix diagnostics before continuing.",
        "diagnostics": [d.to_dict() for d in result.diagnostics],
        "errors": result.errors,
        "warnings": result.warnings,
        "passed": result.passed,
        "tool_available": result.tool_available,
    }
    return f"{LINT_FAILED_PREFIX}{json.dumps(payload, ensure_ascii=False)}"


def is_lint_failure_error(error: str | None) -> bool:
    return bool(error and str(error).startswith(LINT_FAILED_PREFIX))


def parse_lint_failure_error(error: str) -> dict[str, Any] | None:
    if not is_lint_failure_error(error):
        return None
    raw = error[len(LINT_FAILED_PREFIX) :]
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return {"message": raw}


def apply_lint_to_response(
    response: dict[str, Any],
    *,
    file_path: str | Path,
    filename: str | None = None,
    artifact_type: str | None = None,
) -> dict[str, Any]:
    lint_result = run_post_write_sv_lint(
        file_path,
        filename=filename,
        artifact_type=artifact_type,
    )
    if lint_result is None:
        return response
    response = dict(response)
    response["lint"] = lint_result.to_dict()
    if lint_result.has_agent_blocking_issues:
        response["success"] = False
        response["error"] = lint_failure_error_payload(
            lint_result,
            filename=filename or Path(file_path).name,
        )
    return response
