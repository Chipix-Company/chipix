"""Tests for large-spec document context manager."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault(
    "CHIPVERIFY_SECRET_KEY",
    "test-document-context-secret-key",
)

from services.document_context.extract import (
    _split_text_into_virtual_pages,
    write_pages_jsonl,
    read_pages_jsonl,
)
from services.document_context.indexer import build_heuristic_index
from services.document_context.manager import (
    ensure_spec_document_index,
    read_spec_pages,
    search_spec,
    spec_text_for_mental_model,
)
from services.document_context.render import render_index_markdown
from services.document_context.retriever import SpecPageRetriever, load_index_json


def _make_multipage_markdown(tmp_path: Path, pages: int = 25) -> Path:
    lines = ["# Test Specification\n"]
    for i in range(1, pages + 1):
        lines.append(f"\n## Section {i} (page {i})\n")
        lines.append(
            f"REQ-{i:03d}: The module shall support behavior {i} on page {i}.\n"
        )
        lines.append("The system must reset correctly after power-on.\n")
    path = tmp_path / "large_spec.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_virtual_page_splitting():
    text = "\n\n".join(
        f"Paragraph {i} with shall requirement {i}. " + ("detail " * 200)
        for i in range(40)
    )
    pages = _split_text_into_virtual_pages(text)
    assert len(pages) >= 2
    assert pages[0].page_num == 1
    assert all(p.char_count >= 0 for p in pages)


def test_heuristic_index_and_retrieval(tmp_path):
    from services.document_context.extract import extract_pages_from_file

    spec_path = _make_multipage_markdown(tmp_path, pages=30)
    pages = extract_pages_from_file(spec_path, spec_path.name)
    index = build_heuristic_index(
        artifact_id="art-1",
        checksum_sha256="abc",
        filename="large_spec.md",
        pages=pages,
    )
    assert index.page_count == len(pages)
    assert len(index.sections) >= 1

    ctx_dir = tmp_path / "ctx"
    write_pages_jsonl(pages, ctx_dir / "pages.jsonl")
    (ctx_dir / "index.json").write_text(
        json.dumps(index.to_dict(), indent=2),
        encoding="utf-8",
    )
    (ctx_dir / "index.md").write_text(render_index_markdown(index), encoding="utf-8")

    loaded = load_index_json(ctx_dir)
    assert loaded is not None
    assert loaded.page_count == len(pages)

    retriever = SpecPageRetriever(ctx_dir)
    text = retriever.read_pages(2, 3)
    assert "--- PAGE 2 ---" in text
    assert "REQ-002" in text or "Section 2" in text


def test_ensure_spec_document_index_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIPVERIFY_OUTPUTS_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("CHIPVERIFY_DOC_INDEX_MIN_PAGES", "5")

    spec_path = _make_multipage_markdown(tmp_path, pages=20)
    org_id = "org-test"
    project_id = "proj-test"
    artifact_id = "spec-art-1"
    checksum = "deadbeef"

    status = ensure_spec_document_index(
        organization_id=org_id,
        project_id=project_id,
        artifact_id=artifact_id,
        file_path=spec_path,
        filename="large_spec.md",
        checksum_sha256=checksum,
    )
    assert status.status == "ready"
    assert status.page_count >= 5

    pages_result = read_spec_pages(
        organization_id=org_id,
        project_id=project_id,
        artifact_id=artifact_id,
        start_page=1,
        end_page=2,
    )
    assert pages_result["success"] is True
    assert "PAGE 1" in pages_result["text"]

    search_result = search_spec(
        organization_id=org_id,
        project_id=project_id,
        artifact_id=artifact_id,
        query="shall reset",
        limit=5,
    )
    assert search_result["success"] is True

    excerpt = spec_text_for_mental_model(
        organization_id=org_id,
        project_id=project_id,
        artifact_id=artifact_id,
        file_path=spec_path,
        filename="large_spec.md",
        checksum_sha256=checksum,
    )
    assert "SPEC DOCUMENT INDEX" in excerpt or "REQ-" in excerpt
    assert len(excerpt) > 500


def test_read_pages_jsonl_roundtrip(tmp_path):
    pages = _split_text_into_virtual_pages("Hello page one.\n\nSecond page shall pass.")
    path = tmp_path / "pages.jsonl"
    write_pages_jsonl(pages, path)
    loaded = read_pages_jsonl(path)
    assert len(loaded) == len(pages)
    assert loaded[0].page_num == 1
