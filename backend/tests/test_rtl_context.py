"""Tests for RTL context chunking and FTS."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("CHIPVERIFY_SECRET_KEY", "test-rtl-context-secret")

from services.rtl_context.chunker import chunk_rtl_text
from services.rtl_context.manager import ensure_rtl_context_index, search_rtl_context
from services.rtl_context.search_index import search_rtl_fts


def test_chunk_rtl_text_splits_modules():
    source = """
module foo (
  input logic clk
);
  assign x = clk;
endmodule

module bar;
endmodule
"""
    chunks = chunk_rtl_text("top.sv", source)
    assert len(chunks) >= 2
    assert any(c.module_name == "foo" for c in chunks)


def test_rtl_fts_search(tmp_path: Path):
    from services.rtl_context.chunker import RtlChunk
    from services.rtl_context.search_index import build_rtl_fts_index

    db_path = tmp_path / "search.sqlite"
    build_rtl_fts_index(
        db_path,
        [
            RtlChunk(
                filepath="fifo.sv",
                module_name="fifo",
                start_line=1,
                body="module fifo; input logic clk; output logic full;",
            )
        ],
    )
    hits = search_rtl_fts(db_path, "fifo full", limit=3)
    assert hits
    assert hits[0]["module_name"] == "fifo"


def test_ensure_rtl_context_index_single_file(tmp_path: Path, monkeypatch):
    rtl_path = tmp_path / "counter.sv"
    rtl_path.write_text(
        "module counter(input logic clk, output logic [7:0] count);\nendmodule\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("CHIPVERIFY_OUTPUTS_DIR", str(tmp_path / "outputs"))

    result = ensure_rtl_context_index(
        organization_id="org-1",
        project_id="proj-1",
        artifact_id="art-1",
        file_path=rtl_path,
        checksum_sha256="abc123",
    )
    assert result.status == "ready"
    snippets = search_rtl_context("org-1", "proj-1", "art-1", "counter count", limit=2)
    assert snippets
