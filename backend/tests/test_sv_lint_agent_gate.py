"""Agent tool lint gate behavior."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ["CHIPVERIFY_SECRET_KEY"] = "test-sv-lint-agent-gate"
TEST_DB_PATH = Path(__file__).resolve().parent / "test_sv_lint_agent_gate.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.agentic_loop import ToolExecutionResult, ToolRegistry  # noqa: E402
from services.sv_lint.agent_gate import is_lint_failure_error  # noqa: E402
from services.sv_lint.schemas import SvDiagnostic, SvLintResult  # noqa: E402

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def reset_db():
    from database.database import Base, engine

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def _lint_failure_result() -> SvLintResult:
    return SvLintResult(
        passed=False,
        tool_available=True,
        diagnostics=[
            SvDiagnostic(
                line=2,
                col=0,
                end_line=2,
                end_col=3,
                severity="error",
                rule="parse",
                message="syntax error",
            )
        ],
    )


def test_execute_tool_with_retries_skips_lint_failure_retry():
    from services.agentic_loop import AgenticLoopEngine, ToolCall

    engine = AgenticLoopEngine(
        db_session=MagicMock(),
        thread_id="t1",
        websocket_send_fn=lambda *_a, **_k: None,
    )

    async def _run():
        with patch.object(
            engine._tool_registry,
            "execute",
            return_value=ToolExecutionResult(
                success=False,
                error='LINT_FAILED:{"message":"failed","diagnostics":[]}',
            ),
        ):
            result = await engine._execute_tool_with_retries(
                ToolCall(id="1", name="createFile", arguments={})
            )
        assert result.success is False
        assert is_lint_failure_error(result.error)

    import asyncio

    asyncio.run(_run())


def test_create_file_lint_gate_blocks(tmp_path, monkeypatch):
    from database.database import SessionLocal
    from database.models import Organization, Project, User

    monkeypatch.setenv(
        "CHIPVERIFY_OUTPUTS_DIR",
        str(tmp_path / "outputs"),
    )

    db = SessionLocal()
    try:
        user = User(
            id="user1",
            email="u@test.com",
            password_hash="x",
            full_name="Test User",
        )
        org = Organization(
            id="org1",
            owner_user_id=user.id,
            name="Org",
            slug="org-test",
        )
        project = Project(
            id="proj1",
            organization_id=org.id,
            owner_user_id=user.id,
            name="P",
            slug="proj-test",
        )
        db.add_all([user, org, project])
        db.commit()

        registry = ToolRegistry(db, context={"project_id": project.id})
        with patch(
            "services.sv_lint.agent_gate.run_post_write_sv_lint",
            return_value=_lint_failure_result(),
        ):
            result = registry._handlers["createFile"](
                {
                    "project_id": project.id,
                    "filename": "top.sv",
                    "artifact_type": "rtl",
                    "content": "module A; reg a; endmodule\n",
                }
            )
        assert result.success is False
        assert is_lint_failure_error(result.error)
        assert result.result["artifact"]["filename"] == "top.sv"
    finally:
        db.close()


from unittest.mock import MagicMock  # noqa: E402
