"""Project-scoped tab-completion REST API (separate from agent WebSocket chat)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database.database import get_db
from database.models import User
from routes.api import _check_license_validity, _get_current_user, _require_project_access
from services.completion.config import load_completion_config
from services.completion.events import log_completion_event
from services.completion.schemas import CompletionEventRequest, CompletionRequest, CompletionResponse
from services.completion.service import CompletionError, get_completion_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["completions"])


def _user_agent(request: Request) -> Optional[str]:
    return request.headers.get("user-agent")


@router.post("/projects/{project_id}/completions")
async def create_completion(
    project_id: str,
    body: CompletionRequest,
    request: Request,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    _check_license_validity()
    project = _require_project_access(db, current_user, project_id)
    config = load_completion_config()
    if not config.enabled:
        raise HTTPException(status_code=503, detail="Completion service is disabled")

    if body.stream:
        return StreamingResponse(
            _completion_sse_stream(
                db,
                project,
                body,
                user_id=current_user.id,
                user_agent=_user_agent(request),
            ),
            media_type="text/event-stream",
        )

    service = get_completion_service()
    try:
        result = await asyncio.to_thread(
            service.generate,
            db,
            project,
            body,
            user_id=current_user.id,
            user_agent=_user_agent(request),
        )
        payload = result.model_dump()
        headers = {}
        if result.latency_ms is not None:
            headers["X-Completion-Latency-Ms"] = str(result.latency_ms)
        return JSONResponse(content=payload, headers=headers)
    except CompletionError as exc:
        message = str(exc)
        status = 429 if "rate limit" in message.lower() else 400
        raise HTTPException(status_code=status, detail=message) from exc
    except Exception as exc:
        logger.exception("Completion failed for project %s", project_id)
        detail = str(exc).strip() or "Completion inference failed"
        if len(detail) > 240:
            detail = detail[:240] + "..."
        raise HTTPException(status_code=502, detail=detail) from exc


async def _completion_sse_stream(
    db: Session,
    project,
    body: CompletionRequest,
    *,
    user_id: str,
    user_agent: Optional[str],
) -> AsyncIterator[str]:
    service = get_completion_service()
    try:
        async for event in service.generate_stream(
            db,
            project,
            body,
            user_id=user_id,
            user_agent=user_agent,
        ):
            yield f"data: {json.dumps(event)}\n\n"
    except CompletionError as exc:
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"
    except Exception:
        logger.exception("Streaming completion failed")
        yield f"data: {json.dumps({'error': 'Completion inference failed'})}\n\n"


@router.post("/projects/{project_id}/completion-events")
async def record_completion_event(
    project_id: str,
    body: CompletionEventRequest,
    request: Request,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    _check_license_validity()
    _require_project_access(db, current_user, project_id)

    log_completion_event(
        db,
        event_type=body.type,
        completion_id=body.completion_id,
        user_id=current_user.id,
        project_id=project_id,
        language=body.language,
        filepath=body.filepath,
        choice_index=body.choice_index,
        choice_text=body.choice_text,
        select_kind=body.select_kind,
        segments=body.segments,
        user_agent=_user_agent(request),
    )
    db.commit()
    return {"ok": True}
