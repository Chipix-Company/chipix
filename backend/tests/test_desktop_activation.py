import json
import os
from pathlib import Path

import pytest

from utils.desktop_activation import (
    activation_hash,
    expected_machine_key,
    resolve_desktop_activation,
    verify_desktop_activation_from_cache_file,
    verify_desktop_activation_from_env,
)


@pytest.fixture()
def activation_cache(tmp_path, monkeypatch):
    machine_id = "TEST-MACHINE-001"
    machine_key = expected_machine_key(machine_id)
    cache_path = tmp_path / "chipverify-desktop" / "activation.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "machineId": machine_id,
                "activationHash": activation_hash(machine_id, machine_key),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("CHIPVERIFY_DESKTOP_ACTIVATION_BYPASS_LICENSE", raising=False)
    monkeypatch.delenv("CHIPVERIFY_DESKTOP_MACHINE_ID", raising=False)
    monkeypatch.delenv("CHIPVERIFY_DESKTOP_ACTIVATION_HASH", raising=False)
    return machine_id


def test_resolve_desktop_activation_from_cache_file(activation_cache):
    assert verify_desktop_activation_from_env() is None
    resolved = verify_desktop_activation_from_cache_file()
    assert resolved is not None
    assert resolved["valid"] is True
    assert resolved["payload"]["machine_fingerprint"] == activation_cache

    via_resolver = resolve_desktop_activation()
    assert via_resolver is not None
    assert via_resolver["payload"]["license_source"] == "desktop_activation"


def test_resolve_desktop_activation_prefers_env(monkeypatch, activation_cache):
    monkeypatch.setenv("CHIPVERIFY_DESKTOP_ACTIVATION_BYPASS_LICENSE", "true")
    monkeypatch.setenv("CHIPVERIFY_DESKTOP_MACHINE_ID", activation_cache)
    monkeypatch.setenv(
        "CHIPVERIFY_DESKTOP_ACTIVATION_HASH",
        activation_hash(activation_cache, expected_machine_key(activation_cache)),
    )

    resolved = resolve_desktop_activation()
    assert resolved is not None
    assert resolved["payload"]["machine_fingerprint"] == activation_cache
