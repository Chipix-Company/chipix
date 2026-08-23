"""Tests for completion prompt building and snippet budget."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("CHIPVERIFY_SECRET_KEY", "test-completion-secret")

from services.completion.config import CompletionConfig
from services.completion.prompt_builder import PromptBuilder
from services.completion.schemas import Segments, Snippet


@pytest.fixture
def config() -> CompletionConfig:
    return CompletionConfig(
        enabled=True,
        provider="nim",
        model="test-model",
        max_output_tokens=64,
        max_input_chars=2000,
        temperature=0.1,
        timeout_sec=5.0,
        snippet_budget=768,
        server_search_reserve=256,
        fim_template="<PRE> {prefix} <SUF>{suffix} <MID>",
        rate_limit_per_min=120,
        debug=False,
    )


def test_fim_prompt_contains_prefix_suffix(config):
    builder = PromptBuilder(config)
    segments = Segments(prefix="wire clk;\n", suffix="\nendmodule")
    result = builder.build(segments, [])
    assert "wire clk;" in result.prompt
    assert "endmodule" in result.prompt
    assert result.prompt.startswith("<PRE>")


def test_snippet_budget_respected(config):
    builder = PromptBuilder(config)
    snippets = [
        Snippet(filepath="a.sv", body="x" * 500),
        Snippet(filepath="b.sv", body="y" * 500),
    ]
    blocks, remaining = builder.merge_snippets(snippets)
    total = sum(len(block) for block in blocks)
    assert total <= config.snippet_budget
    assert remaining >= 0


def test_client_snippet_order(config):
    builder = PromptBuilder(config)
    segments = Segments(
        declarations=[Snippet(filepath="d.sv", body="module m;")],
        relevant_snippets_from_changed_files=[Snippet(filepath="c.sv", body="changed")],
    )
    ordered = builder.collect_client_snippets(segments)
    assert ordered[0].filepath == "d.sv"
    assert ordered[1].filepath == "c.sv"
