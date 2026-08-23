import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_CORE_ROOT = BACKEND_ROOT / "original_core"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(ORIGINAL_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(ORIGINAL_CORE_ROOT))

from services.verification.plan_presentation import build_verification_plan_presentation
from services.verification.uvm_planner import DesignProfile, plan_uvm_generation


def test_plan_observation_failure_rolls_back_without_aborting(monkeypatch):
    monkeypatch.setenv("CHIPVERIFY_SECRET_KEY", "test-secret-key-for-staged-route-import")
    from routes.staged_verification import _observe_mental_model_event_safely

    class FakeDb:
        rollbacks = 0

        def rollback(self):
            self.rollbacks += 1

    async def failing_observer(_db, **_kwargs):
        raise RuntimeError("observation storage unavailable")

    db = FakeDb()
    result = asyncio.run(
        _observe_mental_model_event_safely(
            failing_observer,
            db,
            event_type="uvm.plan.generated",
        )
    )

    assert result["status"] == "recording_failed"
    assert result["event_type"] == "uvm.plan.generated"
    assert db.rollbacks == 1


def test_plan_presentation_includes_senior_dv_sections():
    plan = {
        "verification_type": "uvm",
        "uvm": {
            "top_module": "fifo",
            "agents": [{"name": "fifo_agent", "description": "Drive and monitor FIFO pins"}],
            "sequences": [
                {
                    "name": "seq_reset_initialization",
                    "description": "Reset the FIFO and check empty/full state",
                    "requirement_ids": ["REQ-001"],
                }
            ],
            "scoreboard_checks": [
                {
                    "check": "check_fifo_ordering",
                    "description": "Compare read data order against write order",
                    "requirement_ids": ["REQ-002"],
                }
            ],
            "coverage_points": [
                {
                    "point": "cp_full_empty",
                    "description": "Cover empty and full status transitions",
                }
            ],
            "files_preview": ["fifo_pkg.sv", "fifo_if.sv", "top_tb.sv"],
        },
    }
    model_content = {
        "design": {
            "top_module": "fifo",
            "description": "Synchronous FIFO with write/read controls and full/empty status.",
            "ports": [{"name": "clk"}, {"name": "rst"}, {"name": "wr"}, {"name": "rd"}],
        },
        "requirements": [
            {"id": "REQ-001", "text": "Reset clears the FIFO."},
            {"id": "REQ-002", "text": "Reads return data in write order."},
            {"id": "REQ-003", "text": "Technical Requirements .................................................................... 3"},
            {"id": "REQ-004", "text": "Project Management Needs ................................................................ 5"},
        ],
    }

    presentation = build_verification_plan_presentation(
        plan,
        verification_type="uvm",
        model_content=model_content,
        spec_filename="fifo_spec.pdf",
        rtl_filename="fifo.v",
    )

    summary = presentation["summary"]
    assert "execution baseline" in summary
    assert "Test objectives" in summary
    assert "Planned test cases" in summary
    assert "Checking strategy" in summary
    assert "Coverage strategy" in summary
    assert "Closure gates" in summary
    assert "seq_reset_initialization" in summary
    assert "Reset clears the FIFO" in summary
    assert "\n\n### Test objectives\n" in summary
    assert "\n\n### Planned test cases\n" in summary
    assert "Technical Requirements" not in summary
    assert "Project Management Needs" not in summary
    assert presentation["planned_test_cases"]
    assert presentation["closure_criteria"]


def test_plan_presentation_humanizes_open_questions_and_risks():
    plan = {
        "verification_type": "uvm",
        "uvm": {
            "top_module": "Bridge_Top",
            "sequences": [{"name": "reset_sequence", "description": "Apply and release reset"}],
        },
    }
    model_content = {
        "design": {
            "top_module": "Bridge_Top",
            "description": "AHB to APB bridge.",
            "ports": [{"name": "Hclk"}, {"name": "Prdata"}],
        },
        "open_questions": [
            {
                "id": "OQ-e0b543e9",
                "question": "Should Prdata be constrained to unsigned values 0 to 255 only?",
                "context": "APB_Interface",
                "blocking": False,
                "answered": False,
                "source_ref": {"file": "rtl_test\\APB_Interface.v", "line": 0},
            }
        ],
        "risks": [
            {
                "type": "nondeterminism",
                "description": "Prdata uses $random, so read data is nondeterministic in simulation.",
            }
        ],
    }

    presentation = build_verification_plan_presentation(
        plan,
        verification_type="uvm",
        model_content=model_content,
        spec_filename="AHB2APB_Verification_Plan.pdf",
        rtl_filename="rtl.zip",
    )

    summary = presentation["summary"]
    assert "APB_Interface: Should Prdata be constrained" in summary
    assert "nondeterminism: Prdata uses $random" in summary
    assert "{'id':" not in summary
    assert "'source_ref':" not in summary
    assert "answered" not in summary


def test_uvm_generation_limits_expand_for_approved_plan_content():
    profile = DesignProfile(port_count=4, complexity_score=12, tier="standard")
    model = SimpleNamespace(
        verification=SimpleNamespace(
            uvm_scenarios=[{"name": f"seq_{idx}"} for idx in range(8)],
            uvm_scoreboard_checks=[{"check": f"check_{idx}"} for idx in range(12)],
            uvm_coverage_points=[{"point": f"cp_{idx}"} for idx in range(10)],
        )
    )

    plan = plan_uvm_generation(profile, model)

    assert plan.content_limits.max_sequence_classes >= 9
    assert plan.content_limits.max_test_classes >= 8
    assert plan.content_limits.max_scoreboard_checks >= 12
    assert plan.content_limits.max_coverage_bins >= 10


def test_generic_uvm_refine_feedback_broadens_plan_without_feedback_named_sequence(monkeypatch):
    monkeypatch.setenv("CHIPVERIFY_SECRET_KEY", "test-secret-key-for-staged-route-import")

    from routes.staged_verification import _refine_uvm_plan

    plan = {
        "verification_type": "uvm",
        "uvm": {
            "top_module": "fifo",
            "agents": [{"name": "fifo_agent"}],
            "sequences": [{"name": "seq_smoke", "description": "Basic smoke"}],
            "scoreboard_checks": [],
            "coverage_points": [],
        },
    }
    content = {
        "design": {
            "top_module": "fifo",
            "ports": [
                {"name": "clk", "width": 1},
                {"name": "rst", "width": 1},
                {"name": "data_in", "width": 8},
                {"name": "data_out", "width": 8},
            ],
            "protocols": [],
        },
        "requirements": [
            {"id": "REQ-001", "text": "Reset clears the FIFO."},
            {"id": "REQ-002", "text": "Reads return data in write order."},
        ],
    }

    result = _refine_uvm_plan(
        plan,
        "can make this plan more clear and covering all the test cases?",
        content,
    )

    refined = result["plan"]["uvm"]
    sequence_names = {item.get("name") for item in refined["sequences"] if isinstance(item, dict)}
    assert "seq_reset_initialization" in sequence_names
    assert "seq_boundary_and_range" in sequence_names
    assert not any(str(name or "").startswith("seq_can_make") for name in sequence_names)
    assert refined["test_case_matrix"]
    assert refined["checking_strategy"]
    assert refined["coverage_strategy"]
    assert refined["closure_criteria"]
