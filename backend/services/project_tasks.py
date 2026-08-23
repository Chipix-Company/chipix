"""Project task board — shared persistence and status helpers."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import ChatMessage, ChatThread, ChatThreadState, Project, ProjectTask, Run, User

TASK_STATUSES = frozenset(
    {"todo", "in_progress", "in_verification", "completed", "blocked", "cancelled"}
)
TASK_PRIORITIES = frozenset({"low", "medium", "high"})
TASK_SOURCES = frozenset({"board", "agent_chat", "verification", "import"})

TERMINAL_RUN_STATUSES = frozenset(
    {"completed", "failed", "blocked", "cancelled", "interrupted"}
)
ACTIVE_RUN_STATUSES = frozenset({"queued", "running"})
TERMINAL_TASK_STATUSES = frozenset({"completed", "blocked", "cancelled"})

DEFAULT_THREAD_TITLES = frozenset(
    {
        "",
        "project chat",
        "agent chat",
        "new chat",
        "new design",
        "untitled conversation",
    }
)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def format_task_display_id(task: ProjectTask) -> str:
    number = int(getattr(task, "display_number", 0) or 0)
    return f"T-{number:04d}"


def derive_agent_name_from_title(title: str, *, fallback: str = "Chip Agent") -> str:
    cleaned = re.sub(r"\s+", " ", (title or "").strip())
    if not cleaned:
        return fallback
    slug = cleaned[:24].strip()
    return f"Design Agent · {slug}"


def derive_title_from_message(message: str, *, max_len: int = 80) -> str:
    first_line = ""
    for line in str(message or "").splitlines():
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break
    if not first_line:
        return "New task"
    trimmed = first_line.rstrip(".!?").strip()
    if len(trimmed) <= max_len:
        return trimmed
    return f"{trimmed[: max_len - 1].strip()}…"


def next_display_number(db: Session, project_id: str) -> int:
    current = (
        db.query(func.max(ProjectTask.display_number))
        .filter(ProjectTask.project_id == project_id)
        .scalar()
    )
    return int(current or 0) + 1


def serialize_project_task(task: ProjectTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "display_id": format_task_display_id(task),
        "display_number": task.display_number,
        "project_id": task.project_id,
        "user_id": task.user_id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "progress_pct": task.progress_pct,
        "thread_id": task.thread_id,
        "run_id": task.run_id,
        "source": task.source,
        "agent_name": task.agent_name,
        "archived": bool(task.archived),
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
        "completed_at": _iso(task.completed_at),
    }


def get_task_for_thread(db: Session, thread_id: str) -> Optional[ProjectTask]:
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if thread and thread.active_task_id:
        task = (
            db.query(ProjectTask)
            .filter(ProjectTask.id == thread.active_task_id)
            .first()
        )
        if task:
            return task
    return (
        db.query(ProjectTask)
        .filter(
            ProjectTask.thread_id == thread_id,
            ProjectTask.archived.is_(False),
        )
        .order_by(ProjectTask.updated_at.desc())
        .first()
    )


def get_task_by_run(db: Session, run_id: str) -> Optional[ProjectTask]:
    return (
        db.query(ProjectTask)
        .filter(ProjectTask.run_id == run_id, ProjectTask.archived.is_(False))
        .order_by(ProjectTask.updated_at.desc())
        .first()
    )


def _task_for_run_via_thread_context(db: Session, run: Run) -> Optional[ProjectTask]:
    """Resolve a board task linked to a run through thread context_run_id."""
    from database.models import ChatThreadState

    states = (
        db.query(ChatThreadState)
        .filter(ChatThreadState.context_run_id == run.id)
        .all()
    )
    for state in states:
        task = get_task_for_thread(db, state.thread_id)
        if task and task.project_id == run.project_id:
            if not task.run_id:
                task.run_id = run.id
            return task
    return None


def link_run_to_thread_task(
    db: Session,
    *,
    thread_id: str,
    run: Run,
    user: Optional[User] = None,
) -> Optional[ProjectTask]:
    """Attach a verification run to the thread's active board task."""
    from database.models import ChatThreadState

    thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if not thread or thread.project_id != run.project_id:
        return None

    state = (
        db.query(ChatThreadState)
        .filter(ChatThreadState.thread_id == thread_id)
        .first()
    )
    if state is None:
        state = ChatThreadState(thread_id=thread_id)
        db.add(state)
    state.context_run_id = run.id

    task = get_task_for_thread(db, thread_id)
    if task:
        task.run_id = run.id
        apply_run_status_to_task(task, run.status)
    elif user is not None:
        task = create_task_for_run(
            db,
            run=run,
            user=user,
            thread_id=thread_id,
            source="verification",
        )
    else:
        return None

    thread.active_task_id = task.id
    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return task


def _is_default_thread_title(title: Optional[str]) -> bool:
    return str(title or "").strip().lower() in DEFAULT_THREAD_TITLES


def _first_user_message(db: Session, thread_id: str) -> Optional[ChatMessage]:
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread_id, ChatMessage.role == "user")
        .order_by(ChatMessage.created_at.asc())
        .first()
    )


def _infer_task_status_for_thread(
    db: Session,
    thread: ChatThread,
    *,
    has_user_messages: bool,
) -> tuple[str, int, Optional[str]]:
    state = (
        db.query(ChatThreadState)
        .filter(ChatThreadState.thread_id == thread.id)
        .first()
    )
    run_id: Optional[str] = None
    if state and state.context_run_id:
        run_id = state.context_run_id
        run = db.query(Run).filter(Run.id == run_id).first()
        if run:
            if run.status in ACTIVE_RUN_STATUSES:
                return "in_verification", 15, run_id
            if run.status == "completed":
                return "completed", 100, run_id
            if run.status in {"failed", "blocked", "interrupted"}:
                return "blocked", 50, run_id

    if has_user_messages:
        return "in_progress", 10, run_id
    return "todo", 0, run_id


def sync_tasks_from_threads(
    db: Session,
    *,
    project: Project,
    user: User,
) -> dict[str, Any]:
    """Backfill board tasks for existing chat threads and orphan verification runs."""
    created = 0
    updated = 0
    skipped = 0

    threads = (
        db.query(ChatThread)
        .filter(
            ChatThread.project_id == project.id,
            ChatThread.user_id == user.id,
        )
        .filter(
            (ChatThread.thread_kind.is_(None))
            | (ChatThread.thread_kind == "main")
            | (ChatThread.thread_kind == "")
        )
        .order_by(ChatThread.updated_at.desc())
        .all()
    )

    for thread in threads:
        existing = get_task_for_thread(db, thread.id)
        first_msg = _first_user_message(db, thread.id)
        has_messages = first_msg is not None

        title = thread.title
        if _is_default_thread_title(title):
            title = derive_title_from_message(
                first_msg.content if first_msg else thread.title
            )

        agent_name = (thread.agent_name or "").strip() or derive_agent_name_from_title(
            title
        )
        if not thread.agent_name:
            thread.agent_name = agent_name

        status, progress_pct, run_id = _infer_task_status_for_thread(
            db,
            thread,
            has_user_messages=has_messages,
        )

        if existing:
            changed = False
            if _is_default_thread_title(existing.title) and title:
                existing.title = title
                changed = True
            if not existing.agent_name and agent_name:
                existing.agent_name = agent_name
                changed = True
            if not existing.thread_id:
                existing.thread_id = thread.id
                changed = True
            if run_id and not existing.run_id:
                existing.run_id = run_id
                changed = True
            if not thread.active_task_id:
                thread.active_task_id = existing.id
                changed = True

            if existing.run_id:
                run = db.query(Run).filter(Run.id == existing.run_id).first()
                if run:
                    before_status = existing.status
                    before_progress = existing.progress_pct
                    apply_run_status_to_task(existing, run.status)
                    if (
                        existing.status != before_status
                        or existing.progress_pct != before_progress
                    ):
                        changed = True
            elif existing.status not in TERMINAL_TASK_STATUSES:
                if status != existing.status:
                    existing.status = status
                    changed = True
                if progress_pct > existing.progress_pct:
                    existing.progress_pct = progress_pct
                    changed = True
                if status == "completed" and not existing.completed_at:
                    existing.completed_at = datetime.now(timezone.utc)
                    changed = True

            if changed:
                existing.updated_at = datetime.now(timezone.utc)
                db.commit()
                updated += 1
            else:
                skipped += 1
            continue

        task = ProjectTask(
            project_id=project.id,
            user_id=user.id,
            display_number=next_display_number(db, project.id),
            title=title or "New task",
            description=(first_msg.content if first_msg else None),
            status=status,
            priority="medium",
            progress_pct=progress_pct,
            thread_id=thread.id,
            run_id=run_id,
            source="import",
            agent_name=agent_name,
        )
        if status == "completed":
            task.completed_at = datetime.now(timezone.utc)
        db.add(task)
        db.flush()
        thread.active_task_id = task.id
        db.commit()
        created += 1

    runs = (
        db.query(Run)
        .filter(Run.project_id == project.id, Run.user_id == user.id)
        .order_by(Run.created_at.desc())
        .all()
    )
    for run in runs:
        if get_task_by_run(db, run.id):
            skipped += 1
            continue
        create_task_for_run(
            db,
            run=run,
            user=user,
            title=derive_title_from_message(run.prompt_text or "Verification run"),
            description=run.prompt_text,
            source="verification",
        )
        created += 1

    tasks = (
        db.query(ProjectTask)
        .filter(
            ProjectTask.project_id == project.id,
            ProjectTask.archived.is_(False),
        )
        .order_by(ProjectTask.updated_at.desc(), ProjectTask.display_number.desc())
        .all()
    )

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "tasks": [serialize_project_task(task) for task in tasks],
    }


def apply_run_status_to_task(task: ProjectTask, run_status: str) -> None:
    status = str(run_status or "").lower()
    now = datetime.now(timezone.utc)
    if status in ACTIVE_RUN_STATUSES:
        task.status = "in_verification"
        task.progress_pct = max(task.progress_pct, 15)
    elif status == "completed":
        task.status = "completed"
        task.progress_pct = 100
        task.completed_at = now
    elif status in {"failed", "blocked", "interrupted"}:
        task.status = "blocked"
        task.progress_pct = max(task.progress_pct, 50)
    elif status == "cancelled":
        task.status = "cancelled"


def upsert_task_for_thread(
    db: Session,
    *,
    thread: ChatThread,
    user: User,
    title: str,
    description: Optional[str] = None,
    agent_name: Optional[str] = None,
    source: str = "agent_chat",
    status: str = "todo",
) -> ProjectTask:
    existing = get_task_for_thread(db, thread.id)
    if existing:
        if title and existing.title in {"", "New task", "Project Chat"}:
            existing.title = title
        if description and not existing.description:
            existing.description = description
        if agent_name and not existing.agent_name:
            existing.agent_name = agent_name
        if not existing.thread_id:
            existing.thread_id = thread.id
        if status == "in_progress" and existing.status in {"todo", "in_progress"}:
            if existing.status == "todo":
                existing.status = "in_progress"
            existing.progress_pct = max(existing.progress_pct, 5)
        thread.active_task_id = existing.id
        if agent_name and not thread.agent_name:
            thread.agent_name = agent_name
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing

    resolved_agent = agent_name or derive_agent_name_from_title(title)
    task = ProjectTask(
        project_id=thread.project_id,
        user_id=user.id,
        display_number=next_display_number(db, thread.project_id),
        title=title or "New task",
        description=description,
        status=status if status in TASK_STATUSES else "todo",
        priority="medium",
        source=source if source in TASK_SOURCES else "agent_chat",
        thread_id=thread.id,
        agent_name=resolved_agent,
    )
    db.add(task)
    db.flush()
    thread.active_task_id = task.id
    if not thread.agent_name:
        thread.agent_name = resolved_agent
    db.commit()
    db.refresh(task)
    return task


def create_task_for_run(
    db: Session,
    *,
    run: Run,
    user: User,
    title: Optional[str] = None,
    description: Optional[str] = None,
    thread_id: Optional[str] = None,
    task_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    source: str = "verification",
) -> ProjectTask:
    if task_id:
        task = db.query(ProjectTask).filter(ProjectTask.id == task_id).first()
        if task:
            task.run_id = run.id
            apply_run_status_to_task(task, run.status)
            if thread_id and not task.thread_id:
                task.thread_id = thread_id
            db.commit()
            db.refresh(task)
            return task

    existing = get_task_by_run(db, run.id)
    if existing:
        apply_run_status_to_task(existing, run.status)
        db.commit()
        db.refresh(existing)
        return existing

    project = db.query(Project).filter(Project.id == run.project_id).first()
    if not project:
        raise ValueError("Project not found for run")

    prompt_title = title or derive_title_from_message(run.prompt_text or "Verification run")
    task = ProjectTask(
        project_id=run.project_id,
        user_id=user.id,
        display_number=next_display_number(db, run.project_id),
        title=prompt_title,
        description=description or run.prompt_text,
        status="in_verification"
        if run.status in ACTIVE_RUN_STATUSES
        else ("completed" if run.status == "completed" else "todo"),
        priority="medium",
        progress_pct=100 if run.status == "completed" else 15,
        thread_id=thread_id,
        run_id=run.id,
        source=source if source in TASK_SOURCES else "verification",
        agent_name=agent_name or "Verification Agent",
    )
    if run.status == "completed":
        task.completed_at = datetime.now(timezone.utc)
    db.add(task)
    if thread_id:
        thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
        if thread:
            thread.active_task_id = task.id
    db.commit()
    db.refresh(task)
    return task


def update_task_status(
    db: Session,
    task: ProjectTask,
    status: str,
    *,
    progress_pct: Optional[int] = None,
) -> ProjectTask:
    if status not in TASK_STATUSES:
        raise ValueError(f"Invalid task status: {status}")
    task.status = status
    if progress_pct is not None:
        task.progress_pct = max(0, min(100, int(progress_pct)))
    if status == "completed":
        task.progress_pct = 100
        task.completed_at = datetime.now(timezone.utc)
    elif status in {"todo", "in_progress", "in_verification"}:
        task.completed_at = None
    db.commit()
    db.refresh(task)
    return task


def create_staged_verification_task(
    db: Session,
    *,
    project: Project,
    user: User,
    verification_type: str,
    approved_plan: Optional[dict] = None,
    thread_id: Optional[str] = None,
) -> ProjectTask:
    plan_title = ""
    if isinstance(approved_plan, dict):
        plan_title = str(approved_plan.get("title") or approved_plan.get("summary") or "").strip()
    title = plan_title or f"{verification_type.replace('_', ' ').title()} verification"

    if thread_id:
        existing = get_task_for_thread(db, thread_id)
        if existing and existing.project_id == project.id:
            existing.status = "in_verification"
            existing.progress_pct = max(existing.progress_pct, 15)
            existing.source = "verification"
            existing.agent_name = "Verification Agent"
            if plan_title and existing.title in {"", "New task"}:
                existing.title = title[:200]
            if approved_plan and not existing.description:
                existing.description = json.dumps(approved_plan)[:8000]
            existing.updated_at = datetime.now(timezone.utc)
            thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
            if thread:
                thread.active_task_id = existing.id
            db.commit()
            db.refresh(existing)
            return existing

    task = ProjectTask(
        project_id=project.id,
        user_id=user.id,
        display_number=next_display_number(db, project.id),
        title=title[:200],
        description=json.dumps(approved_plan)[:8000] if approved_plan else None,
        status="in_verification",
        priority="medium",
        progress_pct=15,
        thread_id=thread_id,
        source="verification",
        agent_name="Verification Agent",
    )
    db.add(task)
    if thread_id:
        thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
        if thread:
            thread.active_task_id = task.id
    db.commit()
    db.refresh(task)
    return task


def finalize_staged_verification_task(
    db: Session,
    task: ProjectTask,
    overall_status: str,
) -> ProjectTask:
    overall = str(overall_status or "").lower()
    if overall == "completed":
        task.status = "completed"
        task.progress_pct = 100
        task.completed_at = datetime.now(timezone.utc)
    elif overall in {"partial", "error", "failed"}:
        task.status = "blocked"
        task.progress_pct = max(task.progress_pct, 50)
    else:
        task.status = "in_verification"
    db.commit()
    db.refresh(task)
    return task


def sync_run_to_linked_task(db: Session, run: Run) -> Optional[ProjectTask]:
    task = get_task_by_run(db, run.id)
    if not task:
        task = _task_for_run_via_thread_context(db, run)
    if not task:
        return None
    apply_run_status_to_task(task, run.status)
    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return task
