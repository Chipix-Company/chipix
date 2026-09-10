#!/usr/bin/env python3
"""
Drive the demo UI in Chrome and leave it ready for OpenScreen recording.

Uses Playwright if available; otherwise prints manual click path.
Does not commit. Demo-only.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "scripts" / "demo" / ".demo_state.json"
FRONTEND = "http://127.0.0.1:5173"


def load_state() -> dict:
    if not STATE.is_file():
        raise SystemExit(f"Missing {STATE}; run seed_sha256_project.py first")
    return json.loads(STATE.read_text(encoding="utf-8"))


def main() -> int:
    state = load_state()
    prompt = state.get("prompt") or (
        "Verify the SHA-256 accelerator end to end"
    )
    project_name = state.get("project_name") or "SHA-256"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Install with:")
        print("  pip install playwright && playwright install chromium")
        print()
        print("Manual walkthrough:")
        print(f"  1. Open {FRONTEND}")
        print(f"  2. Select project '{project_name}'")
        print(f"  3. Paste prompt: {prompt}")
        print("  4. Send and watch tools / mental model / UnitSim / Formal")
        print("  5. Record with: openscreen record --window \"ChipVerify\" ...")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        page.goto(FRONTEND, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_timeout(2500)

        # Dismiss tour if present
        for label in ("Skip", "Skip tour", "Got it", "Close"):
            btn = page.get_by_role("button", name=label)
            if btn.count():
                try:
                    btn.first.click(timeout=1000)
                except Exception:
                    pass

        # Try project switcher / title bar
        try:
            page.locator('[data-tour="titlebar-project"]').click(timeout=3000)
            page.wait_for_timeout(500)
            page.get_by_text(project_name, exact=False).first.click(timeout=5000)
        except Exception:
            print("Could not auto-select project; continuing with current project")

        page.wait_for_timeout(1000)

        # Focus composer and send the demo prompt
        composer = page.locator('[data-tour="composer"] textarea, [data-tour="composer"] [contenteditable="true"], textarea').first
        composer.click(timeout=10_000)
        composer.fill(prompt)
        page.wait_for_timeout(400)
        page.keyboard.press("Control+Enter")

        print("Prompt sent. Watching agent stream for ~90s...")
        # Hold the browser open so OpenScreen can record the live session
        page.wait_for_timeout(95_000)

        # Open mental model + dashboard briefly for end cards
        for tour_id in ("side-rail-mental", "side-rail-dash"):
            try:
                page.locator(f'[data-tour="{tour_id}"]').click(timeout=3000)
                page.wait_for_timeout(4000)
            except Exception:
                pass

        print("Walkthrough interaction finished. Leave the window open for export.")
        page.wait_for_timeout(15_000)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
