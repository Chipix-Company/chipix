#!/usr/bin/env python3
"""Seed an EMPTY SHA-256 demo project (uploads happen on camera)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "backend" / "demo" / "fixtures" / "sha256"
BASE = "http://127.0.0.1:7348/api/v1"


def main() -> int:
    health = requests.get(f"{BASE}/health", timeout=5)
    health.raise_for_status()

    login = requests.post(f"{BASE}/auth/auto-login", timeout=30)
    login.raise_for_status()
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Always create a fresh empty project so uploads are visible on camera.
    name = f"SHA-256 Accelerator {int(time.time()) % 100000}"
    created = requests.post(
        f"{BASE}/projects",
        headers=headers,
        json={
            "name": name,
            "description": "Empty demo project — fixtures uploaded via UI",
        },
        timeout=30,
    )
    created.raise_for_status()
    project = created.json()
    print(f"Created empty project {project['id']} ({project['name']})")

    out = {
        "project_id": project["id"],
        "project_name": project.get("name"),
        "rtl_path": str(FIXTURES / "rtl" / "sha256_accelerator.sv"),
        "spec_path": str(FIXTURES / "spec_sha256.md"),
        "prompts": {
            "mental_model": "Build a mental model from the uploaded SHA-256 spec and RTL.",
            "implement": "Looks right — continue. Write the directed unit testbench for sha256_accelerator.",
            "verify": "Plan and run verification for sha256_accelerator.",
        },
    }
    state_path = ROOT / "scripts" / "demo" / ".demo_state.json"
    state_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"seed failed: {exc}", file=sys.stderr)
        raise
