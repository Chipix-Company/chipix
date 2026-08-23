#!/usr/bin/env python3
"""Generate a redacted diagnostics support bundle for ChipVerify runtime."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REDACTED = "***REDACTED***"
SECRET_NAME_HINTS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASS",
    "PRIVATE",
    "CREDENTIAL",
)


def _is_secret_name(name: str) -> bool:
    upper = name.upper()
    return any(hint in upper for hint in SECRET_NAME_HINTS)


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


def _probe_health(backend_url: str, llm_base_url: str) -> dict:
    backend_health_url = f"{backend_url.rstrip('/')}/health"
    runtime_candidates = _runtime_health_urls(llm_base_url)

    summary: dict[str, object] = {
        "backend_url": backend_health_url,
        "runtime_health_candidates": runtime_candidates,
    }

    try:
        backend_status, backend_payload = _fetch_json(backend_health_url)
        summary["backend"] = {
            "ok": backend_status == 200,
            "status_code": backend_status,
            "payload": backend_payload,
        }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        summary["backend"] = {
            "ok": False,
            "error": str(exc),
        }

    runtime_result: dict[str, object] = {
        "ok": False,
        "url": "",
        "status_code": 0,
        "payload": "",
    }

    for runtime_url in runtime_candidates:
        try:
            runtime_status, runtime_payload = _fetch_json(runtime_url)
            runtime_result = {
                "ok": runtime_status == 200,
                "url": runtime_url,
                "status_code": runtime_status,
                "payload": runtime_payload,
            }
            if runtime_status == 200:
                break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            runtime_result = {
                "ok": False,
                "url": runtime_url,
                "error": str(exc),
            }

    summary["runtime"] = runtime_result
    return summary


def _run_command(
    command: list[str], cwd: Path | None = None, timeout: int = 8
) -> dict[str, object]:
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": result.returncode == 0,
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "command": command,
            "error": str(exc),
        }


def _tail_lines(path: Path, limit: int) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return f"<failed to read {path}: {exc}>"

    lines = content.splitlines()
    if limit > 0:
        lines = lines[-limit:]
    return "\n".join(lines)


def _collect_env_summary() -> dict[str, object]:
    tracked = [
        "CHIPVERIFY_BACKEND_URL",
        "CHIPVERIFY_BACKEND_HOST",
        "CHIPVERIFY_BACKEND_PORT",
        "CHIPVERIFY_ALLOWED_ORIGINS",
        "CHIPVERIFY_SECRET_KEY",
        "CHIPVERIFY_LLM_PROVIDER",
        "CHIPVERIFY_LLM_BASE_URL",
        "CHIPVERIFY_LLM_MODEL_ALIAS",
        "CHIPVERIFY_LLM_API_KEY",
        "CHIPVERIFY_LLM_API_KEY_REQUIRED",
        "CHIPVERIFY_LLM_TIMEOUT_SECONDS",
        "CHIPVERIFY_LLAMACPP_BIN",
        "CHIPVERIFY_LLM_BIND_HOST",
        "CHIPVERIFY_LLM_PORT",
        "CHIPVERIFY_LLM_CONTEXT_SIZE",
        "CHIPVERIFY_LLM_PARALLEL",
        "CHIPVERIFY_ALLOW_PUBLIC_BIND",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_USE_AAD",
        "AZURE_OPENAI_TOKEN_SCOPE",
        "CHIPVERIFY_PYTHON_BIN",
        "CHIPVERIFY_LOG_DIR",
        "CHIPVERIFY_GGUF_PATH",
        "DATABASE_URL",
    ]

    env_summary: dict[str, object] = {}
    for key in tracked:
        value = os.getenv(key)
        if value is None:
            env_summary[key] = {"set": False, "value": None}
            continue

        if _is_secret_name(key):
            env_summary[key] = {
                "set": True,
                "value": REDACTED,
                "length": len(value),
            }
        else:
            env_summary[key] = {"set": True, "value": value}

    return env_summary


def _collect_secret_values(env_summary: dict[str, object]) -> set[str]:
    values: set[str] = set()
    for key, info in env_summary.items():
        if not isinstance(info, dict):
            continue
        if not _is_secret_name(str(key)):
            continue

        raw_value = os.getenv(str(key), "")
        if raw_value and len(raw_value) >= 4:
            values.add(raw_value)

    return values


def _redact_text(text: str, secret_values: set[str]) -> str:
    redacted = text

    for secret_value in sorted(secret_values, key=len, reverse=True):
        redacted = redacted.replace(secret_value, REDACTED)

    redacted = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s\"']+",
        rf"\1{REDACTED}",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+",
        rf"\1{REDACTED}",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(token\s*[=:]\s*)[^\s,;]+",
        rf"\1{REDACTED}",
        redacted,
    )

    return redacted


def _requirements_summary(backend_root: Path) -> list[str]:
    requirements_path = backend_root / "requirements.txt"
    if not requirements_path.exists():
        return []

    lines = requirements_path.read_text(encoding="utf-8", errors="replace").splitlines()
    cleaned: list[str] = []
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        cleaned.append(value)
    return cleaned


def _collect_versions(workspace_root: Path, backend_root: Path) -> dict[str, object]:
    llama_bin = os.getenv("CHIPVERIFY_LLAMACPP_BIN", "llama-server")

    versions = {
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "git": {
            "head": _run_command(["git", "rev-parse", "HEAD"], cwd=workspace_root),
            "branch": _run_command(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=workspace_root,
            ),
            "status": _run_command(["git", "status", "--short"], cwd=workspace_root),
        },
        "llama_server": {
            "binary": llama_bin,
            "version": _run_command([llama_bin, "--version"]),
        },
        "backend_requirements": _requirements_summary(backend_root),
    }

    return versions


def _collect_logs(
    log_dir: Path, bundle_logs_dir: Path, tail_lines: int, secret_values: set[str]
) -> dict[str, object]:
    bundle_logs_dir.mkdir(parents=True, exist_ok=True)

    captured_files: list[str] = []
    summary: dict[str, object] = {
        "log_dir": str(log_dir),
        "captured_files": captured_files,
        "missing": False,
    }

    if not log_dir.exists():
        summary["missing"] = True
        (bundle_logs_dir / "README.txt").write_text(
            f"Log directory does not exist: {log_dir}\n",
            encoding="utf-8",
        )
        return summary

    files = sorted(
        [p for p in log_dir.glob("*.log") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:10]

    if not files:
        (bundle_logs_dir / "README.txt").write_text(
            f"No .log files found in {log_dir}\n",
            encoding="utf-8",
        )
        return summary

    for log_file in files:
        text = _tail_lines(log_file, tail_lines)
        redacted = _redact_text(text, secret_values)
        out_path = bundle_logs_dir / log_file.name
        out_path.write_text(redacted, encoding="utf-8")
        captured_files.append(str(log_file))

    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate redacted diagnostics support bundle for ChipVerify runtime."
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Directory where the diagnostics zip bundle will be written.",
    )
    parser.add_argument(
        "--log-dir",
        default="",
        help="Directory containing runtime/backend logs (defaults to CHIPVERIFY_LOG_DIR or backend/logs).",
    )
    parser.add_argument(
        "--tail-lines",
        type=int,
        default=500,
        help="Number of lines to capture from the end of each log file.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    script_dir = Path(__file__).resolve().parent
    backend_root = script_dir.parent
    workspace_root = backend_root.parent

    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else (script_dir / "support_bundles").resolve()
    )
    log_dir = (
        Path(args.log_dir).resolve()
        if args.log_dir
        else Path(os.getenv("CHIPVERIFY_LOG_DIR", backend_root / "logs")).resolve()
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    env_summary = _collect_env_summary()
    secret_values = _collect_secret_values(env_summary)

    backend_url = os.getenv("CHIPVERIFY_BACKEND_URL", "http://127.0.0.1:7348/api/v1")
    llm_base_url = os.getenv("CHIPVERIFY_LLM_BASE_URL", "http://127.0.0.1:7349/v1")

    health_summary = _probe_health(backend_url, llm_base_url)
    version_summary = _collect_versions(workspace_root, backend_root)

    timestamp = dt.datetime.now(dt.timezone.utc)
    stamp = timestamp.strftime("%Y%m%d-%H%M%S")
    bundle_name = f"chipverify-diagnostics-{stamp}"

    with tempfile.TemporaryDirectory(prefix="chipverify-diag-") as tmp:
        bundle_root = Path(tmp) / bundle_name
        bundle_root.mkdir(parents=True, exist_ok=True)

        logs_summary = _collect_logs(
            log_dir=log_dir,
            bundle_logs_dir=bundle_root / "logs",
            tail_lines=max(1, args.tail_lines),
            secret_values=secret_values,
        )

        summary = {
            "bundle_name": bundle_name,
            "created_at_utc": timestamp.isoformat(),
            "health": health_summary,
            "environment": env_summary,
            "versions": version_summary,
            "logs": logs_summary,
        }

        (bundle_root / "summary.json").write_text(
            json.dumps(summary, indent=2),
            encoding="utf-8",
        )

        archive_path = shutil.make_archive(
            base_name=str(output_dir / bundle_name),
            format="zip",
            root_dir=bundle_root,
        )

    print(f"DIAGNOSTICS_BUNDLE={archive_path}")
    print(f"BUNDLE_CREATED_AT_UTC={timestamp.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
