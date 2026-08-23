"""Tests for workspace_context helpers."""

from services.workspace_context import (
    user_message_requests_overwrite,
    validate_create_file_policy,
)


def test_user_message_requests_overwrite():
    assert user_message_requests_overwrite("Please update my existing FIFO module")
    assert user_message_requests_overwrite("replace the current rtl file")
    assert not user_message_requests_overwrite("Build a new UART from scratch")


def test_validate_create_file_policy_blocks_without_replace():
    class FakeArtifact:
        id = "art-1"
        filename = "fifo.sv"

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def first(self):
            return FakeArtifact()

    class FakeDb:
        def query(self, model):
            return FakeQuery()

    try:
        validate_create_file_policy(
            FakeDb(),
            project_id="proj-1",
            filename="fifo.sv",
            artifact_type="rtl",
            context={
                "project_artifact_manifest": {
                    "active_rtl_artifact_id": "art-1",
                    "active_spec_artifact_id": None,
                }
            },
            replace_existing=False,
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "fifo.sv" in str(exc)
