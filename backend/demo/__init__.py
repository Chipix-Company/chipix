"""Demo-only helpers. Enabled when CHIPVERIFY_DEMO_MODE=true or CHIPVERIFY_LLM_PROVIDER=demo."""

from __future__ import annotations

import os


def demo_mode_enabled() -> bool:
    flag = (os.getenv("CHIPVERIFY_DEMO_MODE") or "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    provider = (
        os.getenv("CHIPVERIFY_LLM_PROVIDER")
        or os.getenv("MODEL_PROVIDER")
        or ""
    ).strip().lower()
    return provider in {"demo", "mock", "script"}


def demo_fix_loop_enabled() -> bool:
    if not demo_mode_enabled():
        return False
    from demo.scenario import SCENARIO_FIX_LOOP, scenario_name

    return scenario_name() == SCENARIO_FIX_LOOP
