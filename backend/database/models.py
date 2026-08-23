import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from database.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_organization_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("organizations.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    owner_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_project_org_slug"),
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    specification_type: Mapped[str] = mapped_column(
        String, nullable=False, default="text"
    )
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="queued", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    execution_time: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    gpu_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    logs_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    output_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    mental_model_revision_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("mental_model_revisions.id"), nullable=True, index=True
    )
    verification_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # RTL Designer integration
    rtl_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    spec_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="upload")


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("runs.id"), nullable=False, index=True
    )
    seq_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # Log line vs lifecycle/system messages (required on SQLite schemas that added this column).
    event_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="log",
        server_default=text("'log'"),
    )
    phase: Mapped[str] = mapped_column(String, nullable=False, default="runtime")
    level: Mapped[str] = mapped_column(String, nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (UniqueConstraint("run_id", "seq_no", name="uq_run_event_seq"),)


class ProjectArtifact(Base):
    __tablename__ = "project_artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    artifact_type: Mapped[str] = mapped_column(
        String, nullable=False, index=True
    )  # spec | rtl
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False, default="upload")
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "artifact_type",
            "revision",
            name="uq_artifact_project_type_rev",
        ),
    )


class ProjectArtifactPointer(Base):
    __tablename__ = "project_artifact_pointers"

    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id"), primary_key=True
    )
    active_spec_artifact_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("project_artifacts.id"), nullable=True
    )
    active_rtl_artifact_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("project_artifacts.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MentalModelRevision(Base):
    __tablename__ = "mental_model_revisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="ready")
    schema_version: Mapped[str] = mapped_column(String, nullable=False, default="1.0")
    source_spec_artifact_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("project_artifacts.id"), nullable=True, index=True
    )
    source_rtl_artifact_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("project_artifacts.id"), nullable=True, index=True
    )
    summary_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "revision",
            name="uq_mental_model_project_revision",
        ),
    )


class PatchProposal(Base):
    __tablename__ = "patch_proposals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    target_artifact_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("project_artifacts.id"), nullable=True, index=True
    )
    source_agent: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    diff_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="awaiting_approval", index=True
    )
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectTask(Base):
    __tablename__ = "project_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    display_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="todo", index=True
    )
    priority: Mapped[str] = mapped_column(
        String, nullable=False, default="medium", index=True
    )
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    thread_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("chat_threads.id"), nullable=True, index=True
    )
    run_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("runs.id"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(
        String, nullable=False, default="board", index=True
    )
    agent_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "display_number",
            name="uq_project_task_display_number",
        ),
    )


class ChatThread(Base):
    __tablename__ = "chat_threads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False, default="Project Chat")
    agent_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    active_task_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("project_tasks.id"), nullable=True, index=True
    )
    thread_kind: Mapped[str] = mapped_column(
        String, nullable=False, default="main", index=True
    )
    parent_thread_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("chat_threads.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChatThreadState(Base):
    __tablename__ = "chat_thread_state"

    thread_id: Mapped[str] = mapped_column(
        String, ForeignKey("chat_threads.id"), primary_key=True
    )
    context_run_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("runs.id"), nullable=True, index=True
    )
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    thread_id: Mapped[str] = mapped_column(
        String, ForeignKey("chat_threads.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChatTokenUsage(Base):
    __tablename__ = "chat_token_usage"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id"), nullable=False, index=True
    )
    thread_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("chat_threads.id"), nullable=True, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    user_message_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("chat_messages.id"), nullable=True, index=True
    )
    assistant_message_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("chat_messages.id"), nullable=True, index=True
    )
    provider: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    source: Mapped[str] = mapped_column(
        String, nullable=False, default="chat", server_default=text("'chat'"), index=True
    )
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class CompletionEvent(Base):
    __tablename__ = "completion_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    completion_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    project_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("projects.id"), nullable=True, index=True
    )
    language: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    filepath: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    choice_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    choice_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    select_kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    segments_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
