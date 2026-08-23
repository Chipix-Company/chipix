#!/usr/bin/env python3
"""Cross-platform runtime readiness check for backend + local model runtime."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def _fetch_json(url: str, timeout: int = 5) -> tuple[int, dict | str]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        status_code = response.getcode()
        body = response.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = body
        return status_code, payload


def _runtime_health_urls(llm_base_url: str) -> list[str]:
    base = llm_base_url.rstrip("/")
    if base.endswith("/v1"):
        root = base[:-3]
        return [f"{root}/health", f"{base}/health"]
    return [f"{base}/health", f"{base}/v1/health"]


def main() -> int:
    backend_url = os.getenv(
        "CHIPVERIFY_BACKEND_URL", "http://127.0.0.1:7348/api/v1"
    ).rstrip("/")
    llm_base_url = os.getenv(
        "CHIPVERIFY_LLM_BASE_URL", "http://127.0.0.1:7349/v1"
    ).rstrip("/")

    backend_health_url = f"{backend_url}/health"
    llm_health_candidates = _runtime_health_urls(llm_base_url)

    errors: list[str] = []

    try:
        backend_status, backend_payload = _fetch_json(backend_health_url)
        if backend_status != 200 or not isinstance(backend_payload, dict):
            errors.append(
                f"Backend health check failed ({backend_status}): {backend_payload}"
            )
        elif backend_payload.get("status") != "ok":
            errors.append(f"Backend health status not ok: {backend_payload}")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        errors.append(f"Backend not reachable at {backend_health_url}: {exc}")

    runtime_ok = False
    runtime_result: str | dict = ""
    runtime_url_used = ""
    for runtime_url in llm_health_candidates:
        try:
            runtime_status, runtime_payload = _fetch_json(runtime_url)
            runtime_url_used = runtime_url
            runtime_result = runtime_payload
            if (
                runtime_status == 200
                and isinstance(runtime_payload, dict)
                and runtime_payload.get("status") == "ok"
            ):
                runtime_ok = True
                break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            continue

    if not runtime_ok:
        errors.append(
            "Local runtime health check failed across candidates "
            f"{llm_health_candidates}; last_result={runtime_result}"
        )

    if errors:
        print("HEALTH_CHECK=FAILED")
        for error in errors:
            print(f"- {error}")
        return 1

    print("HEALTH_CHECK=OK")
    print(f"backend={backend_health_url}")
    print(f"runtime={runtime_url_used}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
