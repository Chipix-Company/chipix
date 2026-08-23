from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any


DEFAULT_LICENSE_SECRET = "chipverify-desktop-demo-license-v1-change-before-release"


def _normalize_code(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _format_groups(prefix: str, value: str, group_count: int) -> str:
    compact = _normalize_code(value)[: group_count * 4]
    groups = [compact[i : i + 4] for i in range(0, len(compact), 4)]
    return f"{prefix}-{'-'.join(groups)}"


def _license_secret() -> str:
    return (os.environ.get("CHIPVERIFY_LICENSE_SECRET") or DEFAULT_LICENSE_SECRET).strip()


def expected_machine_key(machine_id: str, secret: str | None = None) -> str:
    digest = hmac.new(
        (secret or _license_secret()).encode("utf-8"),
        _normalize_code(machine_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()
    return _format_groups("CVK", digest, 6)


def activation_hash(machine_id: str, machine_key: str) -> str:
    raw = f"{_normalize_code(machine_id)}:{_normalize_code(machine_key)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _desktop_activation_cache_path() -> Path | None:
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        return None
    return Path(appdata) / "chipverify-desktop" / "activation.json"


def _build_activation_result(machine_id: str) -> dict[str, Any] | None:
    if not machine_id:
        return None
    return {
        "valid": True,
        "payload": {
            "org_id": "desktop-activated",
            "machine_fingerprint": machine_id,
            "license_source": "desktop_activation",
            "max_seats": 1,
        },
    }


def _verify_desktop_activation_credentials(
    machine_id: str,
    provided_hash: str,
) -> dict[str, Any] | None:
    machine_id = str(machine_id or "").strip()
    provided_hash = str(provided_hash or "").strip()
    if not machine_id or not provided_hash:
        return None

    expected_key = expected_machine_key(machine_id)
    expected_hash = activation_hash(machine_id, expected_key)
    if not hmac.compare_digest(provided_hash, expected_hash):
        return None

    return _build_activation_result(machine_id)


def verify_desktop_activation_from_env() -> dict[str, Any] | None:
    enabled = (
        os.environ.get("CHIPVERIFY_DESKTOP_ACTIVATION_BYPASS_LICENSE", "")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )
    if not enabled:
        return None

    machine_id = os.environ.get("CHIPVERIFY_DESKTOP_MACHINE_ID", "").strip()
    provided_hash = os.environ.get("CHIPVERIFY_DESKTOP_ACTIVATION_HASH", "").strip()
    return _verify_desktop_activation_credentials(machine_id, provided_hash)


def verify_desktop_activation_from_cache_file() -> dict[str, Any] | None:
    """Read Electron desktop activation cache when env vars were not propagated."""
    cache_path = _desktop_activation_cache_path()
    if not cache_path or not cache_path.exists():
        return None

    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None

    machine_id = str(payload.get("machineId") or "").strip()
    provided_hash = str(payload.get("activationHash") or "").strip()
    return _verify_desktop_activation_credentials(machine_id, provided_hash)


def resolve_desktop_activation() -> dict[str, Any] | None:
    """Resolve desktop activation from env first, then the on-disk cache file."""
    return verify_desktop_activation_from_env() or verify_desktop_activation_from_cache_file()
