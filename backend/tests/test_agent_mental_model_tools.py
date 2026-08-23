import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent_tools import mental_model_tools
from services.mental_model import store as mental_model_store


def test_resolve_project_path_uses_rtl_artifact_root_resolver(monkeypatch):
    artifact = SimpleNamespace(id="rtl-artifact")
    seen = {}

    monkeypatch.setattr(
        mental_model_tools,
        "_active_or_latest_artifact",
        lambda project_id, artifact_type, db_session: artifact,
    )

    def fake_rtl_root_path(arg):
        seen["artifact"] = arg
        return r"C:\tmp\chipix\rtl_extracted"

    monkeypatch.setattr(mental_model_store, "_rtl_root_path", fake_rtl_root_path)

    resolved = asyncio.run(mental_model_tools._resolve_project_path("project-1", object()))

    assert resolved == r"C:\tmp\chipix\rtl_extracted"
    assert seen["artifact"] is artifact


def test_load_spec_text_uses_full_spec_reader(monkeypatch):
    artifact = SimpleNamespace(id="spec-artifact")
    seen = {}

    monkeypatch.setattr(
        mental_model_tools,
        "_active_or_latest_artifact",
        lambda project_id, artifact_type, db_session: artifact,
    )

    def fake_read_spec_text(arg):
        seen["artifact"] = arg
        return "parsed PDF/DOCX/plain spec text"

    monkeypatch.setattr(mental_model_store, "_read_spec_text", fake_read_spec_text)

    spec_text = asyncio.run(mental_model_tools._load_spec_text("project-1", object()))

    assert spec_text == "parsed PDF/DOCX/plain spec text"
    assert seen["artifact"] is artifact


def test_active_or_latest_artifact_prefers_active_pointer(monkeypatch):
    class Field:
        def __init__(self, name):
            self.name = name

        def __eq__(self, other):
            return (self.name, other)

        def desc(self):
            return (self.name, "desc")

    class FakePointer:
        project_id = Field("pointer_project_id")

    class FakeArtifact:
        id = Field("artifact_id")
        project_id = Field("artifact_project_id")
        artifact_type = Field("artifact_type")
        revision = Field("revision")

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def first(self):
            return self.rows.pop(0) if self.rows else None

    active_artifact = SimpleNamespace(id="active-rtl")
    latest_artifact = SimpleNamespace(id="latest-rtl")
    pointer = SimpleNamespace(active_rtl_artifact_id="active-rtl", active_spec_artifact_id=None)

    class Session:
        def query(self, model):
            if model is FakePointer:
                return Query([pointer])
            if model is FakeArtifact:
                return Query([active_artifact, latest_artifact])
            raise AssertionError(f"unexpected model: {model}")

    import database.models as models

    monkeypatch.setattr(models, "ProjectArtifactPointer", FakePointer)
    monkeypatch.setattr(models, "ProjectArtifact", FakeArtifact)

    artifact = mental_model_tools._active_or_latest_artifact(
        "project-1",
        "rtl",
        Session(),
    )

    assert artifact is active_artifact
