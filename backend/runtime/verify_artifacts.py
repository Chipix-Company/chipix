#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_executable(command: str) -> Path | None:
    candidate = Path(command)
    if candidate.exists():
        try:
            return candidate.resolve()
        except OSError:
            return candidate

    resolved = shutil.which(command)
    if resolved:
        return Path(resolved).resolve()

    return None


def _load_manifest(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"Manifest file not found: {path}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in manifest: {path} ({exc})")

    schema_version = payload.get("schema_version")
    if schema_version != 1:
        raise ValueError(
            f"Unsupported manifest schema_version={schema_version!r}. Expected 1."
        )

    return payload


def _platform_key() -> str:
    return "windows" if os.name == "nt" else "linux"


def _resolve_manifest_entries(manifest_path: Path, manifest: dict) -> dict:
    base_dir = manifest_path.parent
    platform_key = _platform_key()

    llama_entry = (manifest.get("llama_server") or {}).get(platform_key) or {}
    gguf_entry = manifest.get("gguf") or {}

    llama_rel = str(llama_entry.get("path") or "").strip()
    llama_sha = str(llama_entry.get("sha256") or "").strip().lower()

    gguf_rel = str(gguf_entry.get("path") or "").strip()
    gguf_sha = str(gguf_entry.get("sha256") or "").strip().lower()

    if not llama_rel:
        raise ValueError(
            f"Manifest is missing llama_server.{platform_key}.path (manifest={manifest_path})"
        )
    if not gguf_rel:
        raise ValueError(f"Manifest is missing gguf.path (manifest={manifest_path})")

    return {
        "platform": platform_key,
        "llama_path": (base_dir / llama_rel).resolve(),
        "llama_sha256": llama_sha,
        "gguf_path": (base_dir / gguf_rel).resolve(),
        "gguf_sha256": gguf_sha,
        "model_id": str(gguf_entry.get("model_id") or "").strip(),
    }


def _is_placeholder_sha(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        not normalized
        or normalized == "<sha256>"
        or normalized == "tbd"
        or normalized == "todo"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Verify runtime artifacts (llama-server + GGUF) with sha256 checks."
    )
    parser.add_argument(
        "--manifest",
        help="Path to artifacts manifest JSON (schema_version=1).",
        default="",
    )
    parser.add_argument(
        "--llama-bin",
        help="llama-server executable path or command name.",
        default="",
    )
    parser.add_argument(
        "--gguf-path",
        help="GGUF model file path.",
        default="",
    )
    parser.add_argument(
        "--expected-llama-sha256",
        help="Expected sha256 for llama-server (overrides manifest).",
        default="",
    )
    parser.add_argument(
        "--expected-gguf-sha256",
        help="Expected sha256 for GGUF model file (overrides manifest).",
        default="",
    )
    parser.add_argument(
        "--require-expected",
        action="store_true",
        help="Fail if any expected sha256 value is missing/placeholder.",
    )

    args = parser.parse_args(argv)

    manifest_path = (
        Path(args.manifest).expanduser().resolve() if args.manifest else None
    )
    resolved = None

    if manifest_path:
        manifest = _load_manifest(manifest_path)
        resolved = _resolve_manifest_entries(manifest_path, manifest)

        if not args.llama_bin:
            args.llama_bin = str(resolved["llama_path"])
        if not args.gguf_path:
            args.gguf_path = str(resolved["gguf_path"])

        if not args.expected_llama_sha256:
            args.expected_llama_sha256 = resolved.get("llama_sha256", "")
        if not args.expected_gguf_sha256:
            args.expected_gguf_sha256 = resolved.get("gguf_sha256", "")

    if not args.llama_bin or not args.gguf_path:
        parser.error(
            "Both --llama-bin and --gguf-path are required (or provide --manifest)."
        )

    llama_path = _resolve_executable(args.llama_bin)
    if not llama_path:
        print(
            f"ERROR: llama-server not found: {args.llama_bin!r}. "
            "Provide an absolute path or ensure it is on PATH.",
            file=sys.stderr,
        )
        return 2

    gguf_path = Path(args.gguf_path).expanduser().resolve()
    if not gguf_path.is_file():
        print(f"ERROR: GGUF file not found: {gguf_path}", file=sys.stderr)
        return 2

    expected_llama_sha256 = (args.expected_llama_sha256 or "").strip().lower()
    expected_gguf_sha256 = (args.expected_gguf_sha256 or "").strip().lower()

    if args.require_expected:
        missing = []
        if _is_placeholder_sha(expected_llama_sha256):
            missing.append("llama-server sha256")
        if _is_placeholder_sha(expected_gguf_sha256):
            missing.append("GGUF sha256")
        if missing:
            manifest_note = f" (manifest={manifest_path})" if manifest_path else ""
            print(
                "ERROR: Missing expected sha256 value(s): "
                + ", ".join(missing)
                + manifest_note
                + ".",
                file=sys.stderr,
            )
            return 2

    if expected_llama_sha256 and not _is_placeholder_sha(expected_llama_sha256):
        actual_llama_sha256 = _sha256_file(llama_path)
        if actual_llama_sha256 != expected_llama_sha256:
            print(
                "ERROR: llama-server sha256 mismatch.\n"
                f"- path: {llama_path}\n"
                f"- expected: {expected_llama_sha256}\n"
                f"- actual:   {actual_llama_sha256}",
                file=sys.stderr,
            )
            return 3

    if expected_gguf_sha256 and not _is_placeholder_sha(expected_gguf_sha256):
        actual_gguf_sha256 = _sha256_file(gguf_path)
        if actual_gguf_sha256 != expected_gguf_sha256:
            print(
                "ERROR: GGUF sha256 mismatch.\n"
                f"- path: {gguf_path}\n"
                f"- expected: {expected_gguf_sha256}\n"
                f"- actual:   {actual_gguf_sha256}",
                file=sys.stderr,
            )
            return 3

    model_id = resolved.get("model_id") if resolved else ""
    model_suffix = f" (model_id={model_id})" if model_id else ""
    print(f"OK: artifacts verified{model_suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
