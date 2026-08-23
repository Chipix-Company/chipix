import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.mental_model.living_agent import assess_mental_model_readiness


def _content_with_blocking_question():
    return {
        "design": {
            "top_module": "fifo",
            "ports": [
                {"name": "clk", "direction": "input"},
                {"name": "rst_n", "direction": "input"},
            ],
        },
        "project_scan": {"rtl_files": ["fifo.sv"]},
        "requirements": [{"id": "REQ-001", "text": "FIFO shall reset cleanly."}],
        "open_questions": [
            {
                "id": "OQ-001",
                "question": "Should reset be active high or active low?",
                "blocking": True,
                "answered": False,
            }
        ],
        "build_mode": "llm_enriched",
        "llm_status": "parsed",
    }


def test_demo_readiness_downgrades_open_questions_to_warnings(monkeypatch):
    monkeypatch.delenv("CHIPVERIFY_DEMO_RELAX_MENTAL_MODEL_GATES", raising=False)

    result = assess_mental_model_readiness(
        _content_with_blocking_question(),
        stage="plan",
        verification_type="uvm",
    )

    assert result["safe"] is True
    assert result["status"] == "warning"
    assert result["blockers"] == []
    assert any("Demo mode" in warning for warning in result["warnings"])


def test_strict_readiness_blocks_open_questions(monkeypatch):
    monkeypatch.setenv("CHIPVERIFY_DEMO_RELAX_MENTAL_MODEL_GATES", "false")

    result = assess_mental_model_readiness(
        _content_with_blocking_question(),
        stage="plan",
        verification_type="uvm",
    )

    assert result["safe"] is False
    assert result["status"] == "blocked"
    assert result["blockers"] == ["1 blocking open mental-model question(s) need answers."]
