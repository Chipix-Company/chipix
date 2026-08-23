"""REST API for project-scoped task board."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.database import get_db
from database.models import ChatThread, Project, ProjectTask, User
from routes.api import (
    _check_license_validity,
    _get_current_user,
    _require_project_access,
    _require_thread_access,
    _serialize_chat_thread,
)
from services.project_tasks import (
    TASK_PRIORITIES,
    TASK_STATUSES,
    derive_agent_name_from_title,
    next_display_number,
    serialize_project_task,
    sync_tasks_from_threads,
    update_task_status,
)

router = APIRouter(prefix="/api/v1", tags=["project-tasks"])


class ProjectTaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=8000)
    prompt: Optional[str] = Field(default=None, max_length=8000)
    priority: Optional[str] = Field(default="medium", max_length=16)
    agent_name: Optional[str] = Field(default=None, max_length=120)
    thread_id: Optional[str] = Field(default=None, max_length=64)
    auto_start: bool = False


class ProjectTaskUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=8000)
    status: Optional[str] = Field(default=None, max_length=32)
    priority: Optional[str] = Field(default=None, max_length=16)
    progress_pct: Optional[int] = Field(default=None, ge=0, le=100)
    agent_name: Optional[str] = Field(default=None, max_length=120)
    thread_id: Optional[str] = Field(default=None, max_length=64)
    archived: Optional[bool] = None


class ProjectTaskStartRequest(BaseModel):
    prompt: Optional[str] = Field(default=None, max_length=8000)
    agent_name: Optional[str] = Field(default=None, max_length=120)


def _require_task_access(db: Session, user: User, task_id: str) -> ProjectTask:
    task = (
        db.query(ProjectTask)
        .join(Project, Project.id == ProjectTask.project_id)
        .filter(
            ProjectTask.id == task_id,
            ProjectTask.user_id == user.id,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def _attach_active_task_summary(
    db: Session, thread_payload: dict[str, Any]
) -> dict[str, Any]:
    active_task_id = thread_payload.get("active_task_id")
    if not active_task_id:
        return thread_payload
    task = db.query(ProjectTask).filter(ProjectTask.id == active_task_id).first()
    if not task:
        return thread_payload
    serialized = serialize_project_task(task)
    thread_payload["active_task"] = {
        "id": serialized["id"],
        "display_id": serialized["display_id"],
        "title": serialized["title"],
        "status": serialized["status"],
        "priority": serialized["priority"],
    }
    return thread_payload


@router.get("/projects/{project_id}/tasks")
def list_project_tasks(
    project_id: str,
    status: Optional[str] = Query(default=None),
    assignee_thread_id: Optional[str] = Query(default=None),
    include_archived: bool = Query(default=False),
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
    _license: dict = Depends(_check_license_validity),
):
    project = _require_project_access(db, current_user, project_id)
    query = db.query(ProjectTask).filter(ProjectTask.project_id == project.id)
    if not include_archived:
        query = query.filter(ProjectTask.archived.is_(False))
    if status:
        query = query.filter(ProjectTask.status == status)
    if assignee_thread_id:
        query = query.filter(ProjectTask.thread_id == assignee_thread_id)
    tasks = query.order_by(
        ProjectTask.updated_at.desc(),
        ProjectTask.display_number.desc(),
    ).all()
    return [serialize_project_task(task) for task in tasks]


@router.post("/projects/{project_id}/tasks/sync-from-threads")
def sync_project_tasks_from_threads(
    project_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
    _license: dict = Depends(_check_license_validity),
):
    """Backfill task board rows for existing chat threads and verification runs."""
    project = _require_project_access(db, current_user, project_id)
    return sync_tasks_from_threads(db, project=project, user=current_user)


@router.post("/projects/{project_id}/tasks", status_code=201)
def create_project_task(
    project_id: str,
    req: ProjectTaskCreateRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
    _license: dict = Depends(_check_license_validity),
):
    project = _require_project_access(db, current_user, project_id)
    priority = (req.priority or "medium").lower()
    if priority not in TASK_PRIORITIES:
        raise HTTPException(status_code=400, detail="Invalid priority")

    title = req.title.strip()
    agent_name = (req.agent_name or "").strip() or derive_agent_name_from_title(title)
    initial_status = "todo"
    thread_payload: Optional[dict[str, Any]] = None
    thread_id = (req.thread_id or "").strip() or None

    if req.auto_start or req.prompt:
        thread = ChatThread(
            id=str(uuid.uuid4()),
            project_id=project.id,
            user_id=current_user.id,
            title=title,
            agent_name=agent_name,
            thread_kind="main",
        )
        db.add(thread)
        db.flush()
        thread_id = thread.id
        initial_status = "in_progress"

    task = ProjectTask(
        id=str(uuid.uuid4()),
        project_id=project.id,
        user_id=current_user.id,
        display_number=next_display_number(db, project.id),
        title=title,
        description=req.description or req.prompt,
        status=initial_status,
        priority=priority,
        progress_pct=5 if initial_status == "in_progress" else 0,
        thread_id=thread_id,
        source="board",
        agent_name=agent_name,
    )
    db.add(task)
    db.flush()

    if thread_id:
        thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
        if thread:
            thread.active_task_id = task.id
            thread_payload = _attach_active_task_summary(
                db, _serialize_chat_thread(thread)
            )

    db.commit()
    db.refresh(task)

    response: dict[str, Any] = {"task": serialize_project_task(task)}
    if thread_payload:
        response["thread"] = thread_payload
        response["initial_prompt"] = (req.prompt or req.description or title).strip()
    return response


@router.get("/tasks/{task_id}")
def get_project_task(
    task_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    task = _require_task_access(db, user=current_user, task_id=task_id)
    return serialize_project_task(task)


@router.patch("/tasks/{task_id}")
def patch_project_task(
    task_id: str,
    req: ProjectTaskUpdateRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    task = _require_task_access(db, user=current_user, task_id=task_id)
    fields_set = req.model_fields_set
    if not fields_set:
        raise HTTPException(status_code=400, detail="No update fields were provided")

    if "title" in fields_set:
        next_title = (req.title or "").strip()
        if not next_title:
            raise HTTPException(status_code=400, detail="Task title cannot be empty")
        task.title = next_title

    if "description" in fields_set:
        task.description = (req.description or "").strip() or None

    if "priority" in fields_set:
        priority = (req.priority or "medium").lower()
        if priority not in TASK_PRIORITIES:
            raise HTTPException(status_code=400, detail="Invalid priority")
        task.priority = priority

    if "agent_name" in fields_set:
        task.agent_name = (req.agent_name or "").strip() or None
        if task.thread_id:
            thread = db.query(ChatThread).filter(ChatThread.id == task.thread_id).first()
            if thread and req.agent_name:
                thread.agent_name = task.agent_name

    if "thread_id" in fields_set:
        if req.thread_id:
            thread = _require_thread_access(db, current_user, req.thread_id)
            if thread.project_id != task.project_id:
                raise HTTPException(
                    status_code=400,
                    detail="Thread belongs to another project",
                )
            task.thread_id = thread.id
            thread.active_task_id = task.id
        else:
            task.thread_id = None

    if "archived" in fields_set:
        task.archived = bool(req.archived)

    if "status" in fields_set and req.status:
        update_task_status(
            db,
            task,
            req.status,
            progress_pct=req.progress_pct if "progress_pct" in fields_set else None,
        )
        db.refresh(task)
        return serialize_project_task(task)

    if "progress_pct" in fields_set and req.progress_pct is not None:
        task.progress_pct = max(0, min(100, int(req.progress_pct)))

    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return serialize_project_task(task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_task(
    task_id: str,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    task = _require_task_access(db, user=current_user, task_id=task_id)
    task.archived = True
    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    return None


@router.post("/tasks/{task_id}/start")
def start_project_task(
    task_id: str,
    req: ProjectTaskStartRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
    _license: dict = Depends(_check_license_validity),
):
    task = _require_task_access(db, user=current_user, task_id=task_id)
    project = _require_project_access(db, current_user, task.project_id)

    prompt = (req.prompt or task.description or task.title or "").strip()
    agent_name = (
        (req.agent_name or "").strip()
        or task.agent_name
        or derive_agent_name_from_title(task.title)
    )

    if task.thread_id:
        thread = _require_thread_access(db, current_user, task.thread_id)
    else:
        thread = ChatThread(
            id=str(uuid.uuid4()),
            project_id=project.id,
            user_id=current_user.id,
            title=task.title,
            agent_name=agent_name,
            thread_kind="main",
        )
        db.add(thread)
        db.flush()
        task.thread_id = thread.id

    thread.agent_name = agent_name
    thread.active_task_id = task.id
    task.agent_name = agent_name
    task.status = "in_progress"
    task.progress_pct = max(task.progress_pct, 5)
    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)

    return {
        "task": serialize_project_task(task),
        "thread": _attach_active_task_summary(db, _serialize_chat_thread(thread)),
        "initial_prompt": prompt,
    }
