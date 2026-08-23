"""Tests for svls lint service and agent gate helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.sv_lint.agent_gate import (
    LINT_FAILED_PREFIX,
    apply_lint_to_response,
    is_lint_failure_error,
    lint_failure_error_payload,
    run_post_write_sv_lint,
)
from services.sv_lint.config import (
    DEFAULT_SVLINT_TOML,
    DEFAULT_SVLS_TOML,
    DISABLED_DEFAULT_SVLS_TOML,
    ensure_project_lint_config,
    is_rtl_filename,
    safe_project_relative_path,
    should_lint_artifact,
)
from services.sv_lint.schemas import SvDiagnostic, SvLintResult
from services.sv_lint.service import SvlsLintService

pytestmark = pytest.mark.live


BAD_SV = """module A;

reg a;

always @* begin
    a = b | c;
end

endmodule
"""

GOOD_SV = """module A;

logic a;

always_comb begin
    a = b | c;
end

endmodule
"""


def test_should_lint_artifact_filters_non_rtl():
    assert should_lint_artifact("notes.txt", "rtl") is False
    assert should_lint_artifact("top.sv", "spec") is False
    assert should_lint_artifact("top.sv", "rtl") is True
    assert should_lint_artifact("tb.sv", "generated") is True


def test_is_rtl_filename():
    assert is_rtl_filename("foo.SV")
    assert is_rtl_filename("bar.v")
    assert not is_rtl_filename("doc.pdf")


def test_lint_failure_error_roundtrip():
    result = SvLintResult(
        passed=False,
        tool_available=True,
        diagnostics=[
            SvDiagnostic(
                line=2,
                col=0,
                end_line=2,
                end_col=3,
                severity="warning",
                rule="wire_reg",
                message="use logic",
            )
        ],
    )
    error = lint_failure_error_payload(result, filename="top.sv")
    assert error.startswith(LINT_FAILED_PREFIX)
    assert is_lint_failure_error(error)
    payload = json.loads(error[len(LINT_FAILED_PREFIX) :])
    assert payload["diagnostics"][0]["rule"] == "wire_reg"


def test_apply_lint_to_response_does_not_block_on_warning():
    with patch("services.sv_lint.agent_gate.run_post_write_sv_lint") as mock_lint:
        mock_lint.return_value = SvLintResult(
            passed=True,
            tool_available=True,
            diagnostics=[
                SvDiagnostic(
                    line=1,
                    col=0,
                    end_line=1,
                    end_col=4,
                    severity="warning",
                    rule="legacy_always",
                    message="use always_comb",
                )
            ],
        )
        response = apply_lint_to_response(
            {"success": True, "artifact": {"id": "1"}},
            file_path="/tmp/top.sv",
            filename="top.sv",
            artifact_type="rtl",
        )
    assert response["success"] is True
    assert "lint" in response
    assert "error" not in response


def test_apply_lint_to_response_blocks_on_error():
    with patch("services.sv_lint.agent_gate.run_post_write_sv_lint") as mock_lint:
        mock_lint.return_value = SvLintResult(
            passed=False,
            tool_available=True,
            diagnostics=[
                SvDiagnostic(
                    line=1,
                    col=0,
                    end_line=1,
                    end_col=4,
                    severity="error",
                    rule="parse",
                    message="syntax error",
                )
            ],
        )
        response = apply_lint_to_response(
            {"success": True, "artifact": {"id": "1"}},
            file_path="/tmp/top.sv",
            filename="top.sv",
            artifact_type="rtl",
        )
    assert response["success"] is False
    assert response["error"].startswith(LINT_FAILED_PREFIX)


def test_svls_service_skips_when_binary_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("CHIPVERIFY_REQUIRE_SVLS", raising=False)
    with patch("services.sv_lint.service.resolve_svls_binary", return_value=None):
        service = SvlsLintService()
        result = service.lint_source(
            filepath="top.sv",
            content=BAD_SV,
            project_root=tmp_path,
        )
    assert result.skipped is True
    assert result.tool_available is False
    assert result.passed is True


def test_svls_service_uses_lsp_client(tmp_path):
    diagnostics = [
        SvDiagnostic(
            line=2,
            col=0,
            end_line=2,
            end_col=3,
            severity="error",
            rule="parse",
            message="parse error",
        )
    ]
    with patch("services.sv_lint.service.resolve_svls_binary", return_value="/bin/svls"):
        with patch("services.sv_lint.lsp_client.SvlsLspClient.lint_document", return_value=diagnostics):
            service = SvlsLintService()
            result = service.lint_source(
                filepath="top.sv",
                content=BAD_SV,
                project_root=tmp_path,
            )
    assert result.tool_available is True
    assert result.passed is False
    assert len(result.diagnostics) == 1


def test_svls_service_preserves_relative_path_and_version(tmp_path):
    with patch("services.sv_lint.service.resolve_svls_binary", return_value="/bin/svls"):
        with patch(
            "services.sv_lint.lsp_client.SvlsLspClient.lint_document",
            return_value=[],
        ) as lint_document:
            result = SvlsLintService().lint_source(
                filepath="rtl/sub/top.sv",
                content="module top; endmodule\n",
                project_root=tmp_path,
                version=7,
            )
    assert result.passed is True
    assert lint_document.call_args.kwargs["file_path"] == tmp_path / "rtl" / "sub" / "top.sv"
    assert lint_document.call_args.kwargs["version"] == 7


def test_safe_project_relative_path_blocks_traversal():
    assert safe_project_relative_path("rtl/sub/top.sv") == Path("rtl/sub/top.sv")
    assert safe_project_relative_path("../../outside.sv") == Path("untitled.sv")
    assert safe_project_relative_path("C:/outside/top.sv") == Path("top.sv")


def test_generated_svls_config_migrates_without_overwriting_custom_config(tmp_path):
    config_path = tmp_path / ".svls.toml"
    svlint_path = tmp_path / ".svlint.toml"
    config_path.write_text(DISABLED_DEFAULT_SVLS_TOML, encoding="utf-8")
    ensure_project_lint_config(tmp_path)
    assert config_path.read_text(encoding="utf-8") == DEFAULT_SVLS_TOML
    assert svlint_path.read_text(encoding="utf-8") == DEFAULT_SVLINT_TOML

    custom_svls = "[option]\nlinter = true\n"
    custom_svlint = "[syntaxrules]\nmodule_nonansi_forbidden = true\n"
    config_path.write_text(custom_svls, encoding="utf-8")
    svlint_path.write_text(custom_svlint, encoding="utf-8")
    ensure_project_lint_config(tmp_path)
    assert config_path.read_text(encoding="utf-8") == custom_svls
    assert svlint_path.read_text(encoding="utf-8") == custom_svlint


def test_run_post_write_sv_lint_returns_none_for_non_rtl(tmp_path):
    path = tmp_path / "readme.txt"
    path.write_text("hello", encoding="utf-8")
    assert run_post_write_sv_lint(path, artifact_type="rtl") is None


def test_uri_matches_tolerates_drive_case_and_encoding(tmp_path):
    """Regression: svls may emit a lowercased / percent-encoded drive URI; the
    matcher must still recognize it as the opened file, otherwise every
    diagnostic is silently dropped and clean files are falsely reported."""
    from services.sv_lint.lsp_client import _uri_matches

    target = tmp_path / "top.sv"
    target.write_text("module top; endmodule\n", encoding="utf-8")

    exact = target.resolve().as_uri()
    assert _uri_matches(exact, target) is True
    # An empty or unparsable URI is accepted (permissive) rather than dropped.
    assert _uri_matches("", target) is True
    # A clearly different file is rejected.
    other = (tmp_path / "other.sv").resolve().as_uri()
    assert _uri_matches(other, target) is False

    import os

    if os.name == "nt":
        drive = exact[8]
        rest = exact[10:]  # after the "X:" drive prefix
        svls_style = "file:///" + drive.lower() + "%3A" + rest
        assert _uri_matches(svls_style, target) is True
