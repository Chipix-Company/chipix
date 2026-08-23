import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_CORE_ROOT = BACKEND_ROOT / "original_core"
for path in (BACKEND_ROOT, ORIGINAL_CORE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
os.environ.setdefault("CHIPVERIFY_SECRET_KEY", "test-resolve-agent-task-secret")
os.environ.setdefault("CHIPVERIFY_ALLOW_DEV_AUTH", "true")

from routes.api import _resolve_agent_task


def _event(message: str):
    return SimpleNamespace(message=message, phase="runtime")


@pytest.mark.parametrize(
    ("agent_id", "status", "expected"),
    [
        ("design_reviewer", "running", "Parsing active spec and validating architecture intent"),
        ("design_reviewer", "failed", "Summarizing architecture risks from failed assertions"),
        ("auto_fixer", "queued", "Standing by for compile and assertion results"),
        ("uvm_generator", "completed", "UVM collateral aligned with latest verification output"),
        ("unknown_agent", "idle", "Waiting for mental model and active artifacts"),
    ],
)
def test_resolve_agent_task_static_messages(agent_id, status, expected):
    assert _resolve_agent_task(agent_id, status) == expected


def test_resolve_agent_task_uses_latest_event_message():
    latest = _event("assertion mismatch")
    assert (
        _resolve_agent_task("design_reviewer", "failed", latest_event=latest)
        == "Spec review blocked: assertion mismatch"
    )


def test_resolve_agent_task_uses_agent_event_message():
    agent_event = _event("checking architecture")
    assert (
        _resolve_agent_task(
            "design_reviewer",
            "running",
            recent_events=[agent_event],
            latest_event=_event("ignored"),
        )
        == "Live spec review: checking architecture"
    )
