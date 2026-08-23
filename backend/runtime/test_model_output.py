#!/usr/bin/env python3
"""Smoke-test local llama.cpp model output quality for chip verification prompts."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class PromptResult:
    prompt: str
    output: str
    non_empty: bool
    has_keyword_signal: bool


def _http_json(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    request_headers = headers.copy() if headers else {}
    body: bytes | None = None

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=body, headers=request_headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(
            f"HTTP {exc.code} calling {url}: {details.strip() or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to reach {url}: {exc}") from exc

    if not text.strip():
        return {}

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from {url}: {text[:300]}") from exc


def _extract_content(chat_resp: dict[str, Any]) -> str:
    choices = chat_resp.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first, dict) else {}
    if not isinstance(message, dict):
        return ""

    content = message.get("content")
    return content.strip() if isinstance(content, str) else ""


def _normalize_base_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if not base:
        raise ValueError("base_url must not be empty")
    return base


def _keywords_from_csv(csv_text: str) -> list[str]:
    return [item.strip().lower() for item in csv_text.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test local model output for verification-oriented prompts."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("CHIPVERIFY_LLM_BASE_URL", "http://127.0.0.1:7349/v1"),
        help="OpenAI-compatible base URL ending with /v1",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("CHIPVERIFY_LLM_API_KEY", ""),
        help="Runtime API key (or set CHIPVERIFY_LLM_API_KEY)",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Model id override. Defaults to first id returned by /models.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="Sampling temperature for test prompts.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=512,
        help="Maximum output tokens per prompt.",
    )
    parser.add_argument(
        "--keywords",
        default="assert,property,systemverilog,module,endmodule,always",
        help="Comma-separated output quality signal keywords.",
    )
    parser.add_argument(
        "--min-non-empty",
        type=int,
        default=1,
        help="Minimum number of prompts that must return non-empty content.",
    )
    parser.add_argument(
        "--min-keyword-hits",
        type=int,
        default=1,
        help="Minimum number of prompts that must contain at least one keyword signal.",
    )
    parser.add_argument(
        "--prompt",
        action="append",
        dest="prompts",
        help="Custom prompt(s). Can be provided multiple times.",
    )
    parser.add_argument(
        "--system-prompt",
        default="You are a chip verification assistant. Be concise and output SystemVerilog code immediately. Avoid long hardware descriptions.",
        help="System prompt used for each test request.",
    )

    args = parser.parse_args()
    base_url = _normalize_base_url(args.base_url)
    keywords = _keywords_from_csv(args.keywords)

    prompts = args.prompts or [
        "Write one concise SystemVerilog assertion that grant must arrive within 2 cycles after req rises.",
        "Given a ready/valid interface, provide a short assertion to ensure valid remains stable until ready is high.",
    ]

    headers = {"Accept": "application/json"}
    if args.api_key.strip():
        headers["Authorization"] = f"Bearer {args.api_key.strip()}"

    # Quick runtime health check before model calls.
    root_url = base_url[:-3] if base_url.endswith("/v1") else base_url
    health_resp = _http_json("GET", f"{root_url}/health", headers=headers, timeout=15)
    if health_resp.get("status") != "ok":
        raise RuntimeError(f"Runtime health check failed: {health_resp}")

    models_resp = _http_json("GET", f"{base_url}/models", headers=headers, timeout=20)
    model_id = args.model.strip()
    if not model_id:
        model_data = models_resp.get("data")
        if not isinstance(model_data, list) or not model_data:
            raise RuntimeError("No models returned by /v1/models")
        first = model_data[0] if isinstance(model_data[0], dict) else {}
        model_id = str(first.get("id", "")).strip()
        if not model_id:
            raise RuntimeError("Unable to determine model id from /v1/models")

    print(f"MODEL_ID={model_id}")
    print(f"PROMPT_COUNT={len(prompts)}")

    results: list[PromptResult] = []

    for index, prompt in enumerate(prompts, start=1):
        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": args.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
        }

        chat_resp = _http_json(
            "POST",
            f"{base_url}/chat/completions",
            headers=headers,
            payload=payload,
            timeout=90,
        )
        content = _extract_content(chat_resp)
        print(f"PROMPT_{index}_RESPONSE_START")
        print(content)
        print(f"PROMPT_{index}_RESPONSE_END")

        lowered = content.lower()
        has_keyword_signal = any(word in lowered for word in keywords)
        non_empty = bool(content)

        results.append(
            PromptResult(
                prompt=prompt,
                output=content,
                non_empty=non_empty,
                has_keyword_signal=has_keyword_signal,
            )
        )

        preview = content[:220].replace("\n", " ") if content else "<empty>"
        print(f"PROMPT_{index}_NON_EMPTY={non_empty}")
        print(f"PROMPT_{index}_HAS_KEYWORD_SIGNAL={has_keyword_signal}")
        print(f"PROMPT_{index}_PREVIEW={preview}")

    non_empty_count = sum(1 for item in results if item.non_empty)
    keyword_hits = sum(1 for item in results if item.has_keyword_signal)

    print(f"NON_EMPTY_COUNT={non_empty_count}")
    print(f"KEYWORD_HIT_COUNT={keyword_hits}")

    passed = (
        non_empty_count >= args.min_non_empty and keyword_hits >= args.min_keyword_hits
    )

    if passed:
        print("SMOKE_TEST_RESULT=PASS")
        return 0

    print("SMOKE_TEST_RESULT=FAIL")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover
        print(f"SMOKE_TEST_ERROR={exc}")
        raise SystemExit(1)
