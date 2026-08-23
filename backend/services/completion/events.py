"""Completion event persistence."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from database.models import CompletionEvent

logger = logging.getLogger(__name__)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def log_completion_event(
    db: Session,
    *,
    event_type: str,
    completion_id: str,
    user_id: str,
    project_id: Optional[str] = None,
    language: Optional[str] = None,
    filepath: Optional[str] = None,
    choice_index: Optional[int] = None,
    choice_text: Optional[str] = None,
    select_kind: Optional[str] = None,
    segments: Optional[dict[str, Any]] = None,
    model: Optional[str] = None,
    latency_ms: Optional[int] = None,
    user_agent: Optional[str] = None,
) -> CompletionEvent:
    segments_json = None
    if segments:
        redacted = {
            "prefix_hash": _hash_text(str(segments.get("prefix") or "")),
            "suffix_hash": _hash_text(str(segments.get("suffix") or "")),
            "filepath": segments.get("filepath"),
        }
        segments_json = json.dumps(redacted)

    row = CompletionEvent(
        completion_id=completion_id,
        event_type=event_type,
        user_id=user_id,
        project_id=project_id,
        language=language,
        filepath=filepath,
        choice_index=choice_index,
        choice_text=(choice_text or "")[:2000] if choice_text else None,
        select_kind=select_kind,
        segments_json=segments_json,
        model=model,
        latency_ms=latency_ms,
        user_agent=user_agent,
    )
    db.add(row)
    db.flush()
    return row
