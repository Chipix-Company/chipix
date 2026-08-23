#!/usr/bin/env python3
"""Validate ChipVerify HTTPS fronting for enterprise deployments."""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


def _build_health_url(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "https":
        raise ValueError("base URL must use https://")

    raw_path = (parsed.path or "").rstrip("/")
    if raw_path.endswith("/api/v1"):
        health_path = f"{raw_path}/health"
    elif raw_path.endswith("/api/v1/health"):
        health_path = raw_path
    elif raw_path:
        health_path = f"{raw_path}/api/v1/health"
    else:
        health_path = "/api/v1/health"

    rebuilt = parsed._replace(path=health_path, params="", query="", fragment="")
    return urllib.parse.urlunparse(rebuilt)


def _build_ssl_context(ca_bundle: str | None, insecure: bool) -> ssl.SSLContext:
    if insecure:
        return ssl._create_unverified_context()  # noqa: SLF001
    if ca_bundle:
        return ssl.create_default_context(cafile=ca_bundle)
    return ssl.create_default_context()


def validate_endpoint(
    base_url: str,
    timeout: float,
    require_hsts: bool,
    ca_bundle: str | None,
    insecure: bool,
) -> int:
    try:
        health_url = _build_health_url(base_url)
    except ValueError as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 2

    context = _build_ssl_context(ca_bundle, insecure)

    request = urllib.request.Request(
        health_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "chipverify-tls-validator/1.0",
        },
    )

    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=context
        ) as response:  # noqa: S310
            status_code = response.getcode()
            headers = response.headers
            payload_raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        print(f"Validation failed: HTTP {exc.code} from {health_url}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(
            f"Validation failed: unable to reach {health_url}: {exc}", file=sys.stderr
        )
        return 1
    except TimeoutError:
        print(
            f"Validation failed: timeout while connecting to {health_url}",
            file=sys.stderr,
        )
        return 1

    if status_code != 200:
        print(
            f"Validation failed: expected HTTP 200, got {status_code}", file=sys.stderr
        )
        return 1

    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError as exc:
        print(
            f"Validation failed: health response was not valid JSON: {exc}",
            file=sys.stderr,
        )
        return 1

    if not isinstance(payload, dict) or payload.get("status") != "ok":
        print(
            "Validation failed: expected JSON object with {'status': 'ok'} in health response.",
            file=sys.stderr,
        )
        return 1

    if require_hsts and not headers.get("Strict-Transport-Security"):
        print(
            "Validation failed: missing Strict-Transport-Security header.",
            file=sys.stderr,
        )
        return 1

    print("TLS validation passed.")
    print(f"- Health URL: {health_url}")
    print(f"- HTTP status: {status_code}")
    print(f"- Health status: {payload.get('status')}")
    if require_hsts:
        print(f"- HSTS: {headers.get('Strict-Transport-Security')}")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate HTTPS reverse-proxy fronting for ChipVerify runtime."
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="Public HTTPS base URL (for example: https://chipverify.example.com)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="Request timeout in seconds (default: 8)",
    )
    parser.add_argument(
        "--require-hsts",
        action="store_true",
        help="Fail if Strict-Transport-Security header is missing.",
    )
    parser.add_argument(
        "--ca-bundle",
        default=None,
        help="Optional CA bundle path for private enterprise PKI.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification (lab-only).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return validate_endpoint(
        base_url=args.base_url,
        timeout=args.timeout,
        require_hsts=args.require_hsts,
        ca_bundle=args.ca_bundle,
        insecure=args.insecure,
    )


if __name__ == "__main__":
    raise SystemExit(main())
