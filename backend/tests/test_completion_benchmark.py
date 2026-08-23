"""Benchmark completion latency across providers/models."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

os.environ.setdefault("CHIPVERIFY_SECRET_KEY", "test-completion-benchmark")
load_dotenv()
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from services.completion.config import CompletionConfig
from services.completion.prompt_builder import PromptBuilder
from services.completion.schemas import Segments
from services.completion.stop_conditions import create_stop_condition

pytestmark = pytest.mark.live


def _prompt() -> str:
    cfg = CompletionConfig(
        enabled=True,
        provider="gemini",
        model="gemini-2.5-flash",
        max_output_tokens=32,
        max_input_chars=8192,
        temperature=0.1,
        timeout_sec=3.0,
        snippet_budget=768,
        server_search_reserve=256,
        fim_template="<PRE> {prefix} <SUF>{suffix} <MID>",
        rate_limit_per_min=120,
        debug=False,
    )
    builder = PromptBuilder(cfg)
    segments = Segments(
        prefix=(
            "module counter (\n"
            "  input logic clk,\n"
            "  output logic [7:0] count\n"
            ");\n"
            "  always_ff @(posedge clk) count <= "
        ),
        suffix=";\nendmodule\n",
        filepath="counter.sv",
    )
    return builder.build(segments, []).prompt


@pytest.mark.parametrize(
    "provider,model",
    [
        ("bedrock", "deepseek.v3.2"),
        ("gemini", "gemini-2.5-flash"),
        pytest.param("nim", "deepseek-ai/deepseek-coder-6.7b-instruct", marks=pytest.mark.skip(reason="NIM key lacks model access on this machine")),
    ],
)
def test_fim_latency_budget(provider: str, model: str):
    if provider == "bedrock" and not (
        os.getenv("BEDROCK_API_KEY") or os.getenv("AWS_BEARER_TOKEN_BEDROCK")
    ):
        pytest.skip("Bedrock API key not configured")
    if provider == "gemini" and not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
        pytest.skip("Gemini API key not configured")

    from llm_provider import complete_fim_sync

    prompt = _prompt()
    started = time.perf_counter()
    try:
        text, usage = complete_fim_sync(
            prompt,
            provider=provider,
            model=model,
            max_tokens=32,
            timeout_sec=3.0,
        )
    except Exception as exc:
        message = str(exc).lower()
        if "429" in message or "quota" in message or "resource_exhausted" in message:
            pytest.skip(f"Provider quota exhausted: {exc}")
        raise

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    trimmed = create_stop_condition("systemverilog").apply(text or "")

    print(f"[benchmark] provider={provider} model={model} elapsed_ms={elapsed_ms} text={trimmed!r}")

    assert trimmed.strip(), f"Empty completion from {provider}/{model}"
    assert "```" not in trimmed
    assert elapsed_ms < 5000, f"Too slow: {elapsed_ms}ms for {provider}/{model}"
    if usage:
        assert usage.total_tokens > 0
