"""Smoke test an Azure AI/OpenAI-compatible chat deployment.

The script intentionally reads secrets from env or an env file and never prints
the key. It is useful for checking whether a packaged/demo deployment name
actually exists before rebuilding the desktop app.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable


def _load_env_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {path}")
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _candidate_models(raw: str | None) -> list[str]:
    if not raw:
        raw = ",".join(
            value
            for value in (
                os.getenv("AZURE_OPENAI_DEPLOYMENT"),
                os.getenv("AZURE_OPENAI_MODEL"),
                os.getenv("MODEL_NAME"),
                os.getenv("CHIPVERIFY_LLM_MODEL_ALIAS"),
            )
            if value
        )
    seen: set[str] = set()
    models: list[str] = []
    for item in (raw or "").split(","):
        model = item.strip()
        if model and model not in seen:
            seen.add(model)
            models.append(model)
    return models


def _compact_error(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None) or str(exc)
    pieces = []
    if status is not None:
        pieces.append(f"status={status}")
    if code:
        pieces.append(f"code={code}")
    pieces.append(f"message={message}")
    return " | ".join(pieces)


def _uses_max_completion_tokens(model: str) -> bool:
    name = str(model or "").strip().lower()
    return name.startswith(("gpt-5", "o1", "o3", "o4"))


def _print_config(endpoint: str, models: Iterable[str]) -> None:
    print(f"endpoint={endpoint}")
    print(f"models={','.join(models)}")
    print(f"api_key_present={bool(os.getenv('AZURE_OPENAI_API_KEY') or os.getenv('CHIPVERIFY_LLM_API_KEY'))}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", help="Optional .env file to load before testing")
    parser.add_argument("--endpoint", help="Azure OpenAI-compatible base URL")
    parser.add_argument(
        "--models",
        help="Comma-separated deployment/model names to test, in order",
    )
    args = parser.parse_args()

    if args.env_file:
        _load_env_file(Path(args.env_file))

    endpoint = (
        args.endpoint
        or os.getenv("AZURE_OPENAI_ENDPOINT")
        or os.getenv("CHIPVERIFY_LLM_BASE_URL")
        or os.getenv("OPENAI_API_BASE")
        or ""
    ).rstrip("/")
    api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("CHIPVERIFY_LLM_API_KEY")
    models = _candidate_models(args.models)

    _print_config(endpoint, models)

    if not endpoint:
        print("ERROR: endpoint is missing")
        return 2
    if not api_key:
        print("ERROR: AZURE_OPENAI_API_KEY or CHIPVERIFY_LLM_API_KEY is missing")
        return 3
    if not models:
        print("ERROR: no model/deployment names supplied")
        return 4

    try:
        from openai import OpenAI
    except ImportError as exc:
        print(f"ERROR: openai package is not installed: {exc}")
        return 5

    client = OpenAI(base_url=endpoint, api_key=api_key)
    first_ok = ""
    for model in models:
        try:
            kwargs = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": "Reply with exactly: CHIPIX_AZURE_OK",
                    }
                ],
            }
            if _uses_max_completion_tokens(model):
                kwargs["max_completion_tokens"] = 16
            else:
                kwargs["max_tokens"] = 16
                kwargs["temperature"] = 0
            completion = client.chat.completions.create(**kwargs)
            content = (completion.choices[0].message.content or "").strip()
            print(f"PASS model={model} response={content}")
            first_ok = first_ok or model
        except Exception as exc:  # noqa: BLE001 - smoke script should report SDK errors cleanly.
            print(f"FAIL model={model} {_compact_error(exc)}")

    if first_ok:
        print(f"first_working_model={first_ok}")
        return 0
    return 6


if __name__ == "__main__":
    raise SystemExit(main())
