"""Tiny Bedrock smoke test for ChipVerify's LLM provider path."""

from __future__ import annotations

import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import llm_provider


def main() -> int:
    provider = llm_provider._normalize_provider(llm_provider.PROVIDER)
    model = llm_provider._resolve_model_name(None, provider)
    ok, message = llm_provider.validate_provider()

    print(f"provider={provider}")
    print(f"model={model}")
    print(f"configured={ok}")
    print(f"config_message={message}")

    if provider != "bedrock":
        print("ERROR: provider is not bedrock")
        return 2
    if not ok:
        return 3

    content, reasoning = llm_provider.generate(
        "You are a terse API smoke-test responder.",
        "Reply with exactly: BEDROCK_OK",
        provider="bedrock",
        model=model,
        temperature=0.0,
        max_tokens=16,
    )
    combined = f"{reasoning}\n{content}".strip()
    print(f"response={combined}")
    if "BEDROCK_OK" not in combined:
        print("ERROR: expected BEDROCK_OK in response")
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
