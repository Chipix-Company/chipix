"""Tests for Chip-Verify codebase graph service."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_merge_extractions_dedupes_nodes():
    from services.codebase_graph.builder import _merge_extractions
    from services.codebase_graph.schemas import WorkerResult

    parts = [
        WorkerResult(name="a", nodes=[{"id": "x", "label": "mod"}], edges=[]),
        WorkerResult(name="b", nodes=[{"id": "x", "label": "mod"}], edges=[]),
    ]
    merged = _merge_extractions(parts)
    assert len(merged["nodes"]) == 1


def test_get_build_status_missing_project():
    from services.codebase_graph.manager import get_build_status

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    status = get_build_status(db, "missing-id")
    assert status["status"] == "pending"


def test_worker_spec_links_modules(tmp_path):
    from services.codebase_graph.builder import _worker_spec

    spec = tmp_path / "spec.md"
    spec.write_text("# CSR Requirements\n\nThe fifo module must handle backpressure.\n", encoding="utf-8")
    result = _worker_spec([spec], {"fifo"})
    relations = {e["relation"] for e in result.edges}
    assert "defines" in relations
    assert "requires" in relations


def test_worker_slang_emits_instantiates_edges(tmp_path):
    """Regression: the slang worker must read the analyzer's ``instantiations``
    key (not the non-existent ``instances``) or hierarchy edges vanish."""
    from services.codebase_graph import builder as graph_builder

    top = tmp_path / "top.sv"
    top.write_text("module top; child u_c(); endmodule\n", encoding="utf-8")

    fake_result = MagicMock()
    fake_result.module_index = {
        "top": {
            "file": "top.sv",
            "ports": [{"name": "clk", "direction": "input"}],
            "instantiations": [
                {"module": "child", "instance": "u_c", "connections": []},
            ],
        },
        "child": {"file": "top.sv", "ports": [], "instantiations": []},
    }

    fake_analyzer = MagicMock()
    fake_analyzer.analyze_project.return_value = fake_result

    with patch(
        "services.mental_model.slang_analyzer.SlangStructuralAnalyzer",
        return_value=fake_analyzer,
    ):
        result = graph_builder._worker_slang(tmp_path, [top])

    inst_edges = [e for e in result.edges if e["relation"] == "instantiates"]
    assert len(inst_edges) == 1
    assert inst_edges[0]["source"] == "slang:module:top"
    assert inst_edges[0]["target"] == "slang:module:child"


def test_collect_spec_files_does_not_read_research_folder(tmp_path):
    from services.codebase_graph.builder import _collect_spec_files

    uploaded = tmp_path / "uploaded"
    uploaded.mkdir()
    spec = uploaded / "design.md"
    spec.write_text("# Design", encoding="utf-8")

    research = tmp_path / "r_n_d" / "issues_to_check"
    research.mkdir(parents=True)
    internal_issue = research / "internal.md"
    internal_issue.write_text("# Internal", encoding="utf-8")

    collected = _collect_spec_files(uploaded, tmp_path)

    assert collected == [spec]
    assert internal_issue not in collected
