"""Completion service configuration from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CompletionConfig:
    enabled: bool
    provider: str
    model: str
    max_output_tokens: int
    max_input_chars: int
    temperature: float
    timeout_sec: float
    snippet_budget: int
    server_search_reserve: int
    fim_template: str
    rate_limit_per_min: int
    debug: bool
    fim_token: str = "<|FIM|>"


def _resolve_completion_provider() -> str:
    explicit = os.getenv("CHIPVERIFY_COMPLETION_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if os.getenv("BEDROCK_API_KEY") or os.getenv("AWS_BEARER_TOKEN_BEDROCK"):
        return "bedrock"
    if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("NIM_API_KEY") or os.getenv("CHIPVERIFY_LLM_API_KEY"):
        return "nim"
    if os.getenv("OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_API_KEY"):
        return "openai"
    return "bedrock"


def _resolve_completion_model(provider: str) -> str:
    explicit = os.getenv("CHIPVERIFY_COMPLETION_MODEL", "").strip()
    if explicit:
        return explicit
    if provider == "bedrock":
        return (
            os.getenv("BEDROCK_COMPLETION_MODEL")
            or os.getenv("CHIPVERIFY_BEDROCK_COMPLETION_MODEL")
            or "openai.gpt-oss-20b"
        )
    if provider == "gemini":
        return os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
    if provider == "nim":
        return os.getenv("NIM_MODEL") or "meta/llama-3.1-70b-instruct"
    return os.getenv("MODEL_NAME") or "gpt-4o-mini"


def load_completion_config() -> CompletionConfig:
    provider = _resolve_completion_provider()
    return CompletionConfig(
        enabled=str(os.getenv("CHIPVERIFY_COMPLETION_ENABLED", "true")).lower()
        in {"1", "true", "yes", "on"},
        provider=provider,
        model=_resolve_completion_model(provider),
        max_output_tokens=int(os.getenv("CHIPVERIFY_COMPLETION_MAX_OUTPUT_TOKENS", "64")),
        max_input_chars=int(os.getenv("CHIPVERIFY_COMPLETION_MAX_INPUT_CHARS", "8192")),
        temperature=float(os.getenv("CHIPVERIFY_COMPLETION_TEMPERATURE", "0.1")),
        timeout_sec=float(
            os.getenv(
                "CHIPVERIFY_COMPLETION_TIMEOUT_SEC",
                "12" if provider == "bedrock" else "3",
            )
        ),
        snippet_budget=int(os.getenv("CHIPVERIFY_COMPLETION_SNIPPET_BUDGET", "768")),
        server_search_reserve=int(
            os.getenv("CHIPVERIFY_COMPLETION_SERVER_SEARCH_RESERVE", "256")
        ),
        fim_template=os.getenv(
            "CHIPVERIFY_COMPLETION_FIM_TEMPLATE",
            "<PRE> {prefix} <SUF>{suffix} <MID>",
        ),
        rate_limit_per_min=int(os.getenv("CHIPVERIFY_COMPLETION_RATE_LIMIT_PER_MIN", "600")),
        debug=str(os.getenv("CHIPVERIFY_COMPLETION_DEBUG", "false")).lower()
        in {"1", "true", "yes", "on"},
    )
