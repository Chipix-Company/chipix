from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import ChatThread, ChatTokenUsage, Project

logger = logging.getLogger(__name__)


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _usage_to_dict(usage: Any) -> dict:
    if usage is None:
        return {}
    if hasattr(usage, "to_dict"):
        try:
            return usage.to_dict()
        except Exception:
            logger.debug("Failed to serialize usage via to_dict", exc_info=True)
    if isinstance(usage, dict):
        return dict(usage)
    return {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "total_tokens": getattr(usage, "total_tokens", 0),
        "provider": getattr(usage, "provider", None),
        "model": getattr(usage, "model", None),
        "is_estimated": getattr(usage, "is_estimated", False),
        "details": getattr(usage, "details", None) or {},
    }


def serialize_token_usage(row: ChatTokenUsage) -> dict:
    metadata = {}
    if row.metadata_json:
        try:
            metadata = json.loads(row.metadata_json)
        except json.JSONDecodeError:
            metadata = {}
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "project_id": row.project_id,
        "thread_id": row.thread_id,
        "user_message_id": row.user_message_id,
        "assistant_message_id": row.assistant_message_id,
        "provider": row.provider,
        "model": row.model,
        "source": row.source,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "total_tokens": row.total_tokens,
        "is_estimated": bool(row.is_estimated),
        "metadata": metadata,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def summarize_token_usage(rows: list[ChatTokenUsage]) -> dict:
    by_provider: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_entries": 0,
        "entries": len(rows),
    }

    def add_bucket(bucket: dict[str, dict], key: str, row: ChatTokenUsage) -> None:
        item = bucket.setdefault(
            key or "unknown",
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "entries": 0,
            },
        )
        item["input_tokens"] += row.input_tokens
        item["output_tokens"] += row.output_tokens
        item["total_tokens"] += row.total_tokens
        item["entries"] += 1

    for row in rows:
        totals["input_tokens"] += row.input_tokens
        totals["output_tokens"] += row.output_tokens
        totals["total_tokens"] += row.total_tokens
        if row.is_estimated:
            totals["estimated_entries"] += 1
        add_bucket(by_provider, row.provider or "unknown", row)
        add_bucket(by_model, row.model or "unknown", row)

    return {
        **totals,
        "by_provider": by_provider,
        "by_model": by_model,
    }


def persist_chat_token_usage(
    db: Session,
    *,
    thread_id: str,
    user_id: str,
    usage: Any,
    user_message_id: Optional[str] = None,
    assistant_message_id: Optional[str] = None,
    source: str = "chat",
    metadata: Optional[dict] = None,
) -> Optional[ChatTokenUsage]:
    usage_data = _usage_to_dict(usage)
    if not usage_data:
        return None

    input_tokens = _as_int(
        usage_data.get("input_tokens", usage_data.get("inputTokens"))
    )
    output_tokens = _as_int(
        usage_data.get("output_tokens", usage_data.get("outputTokens"))
    )
    total_tokens = _as_int(
        usage_data.get("total_tokens", usage_data.get("totalTokens"))
    )
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens
    if total_tokens <= 0:
        return None

    thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if not thread:
        return None
    project = db.query(Project).filter(Project.id == thread.project_id).first()
    if not project:
        return None

    details = usage_data.get("details") if isinstance(usage_data.get("details"), dict) else {}
    metadata_payload = {
        **details,
        **(metadata or {}),
    }

    row = ChatTokenUsage(
        organization_id=project.organization_id,
        project_id=thread.project_id,
        thread_id=thread.id,
        user_id=user_id,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        provider=usage_data.get("provider"),
        model=usage_data.get("model"),
        source=source,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        is_estimated=bool(usage_data.get("is_estimated", usage_data.get("isEstimated", False))),
        metadata_json=json.dumps(metadata_payload) if metadata_payload else None,
    )
    db.add(row)
    db.flush()
    _export_langfuse(row, metadata_payload)
    return row


def persist_project_token_usage(
    db: Session,
    *,
    project_id: str,
    user_id: str,
    usage: Any,
    source: str = "completion",
    metadata: Optional[dict] = None,
) -> Optional[ChatTokenUsage]:
    """Persist token usage for project-scoped features without a chat thread."""
    usage_data = _usage_to_dict(usage)
    if not usage_data:
        return None

    input_tokens = _as_int(
        usage_data.get("input_tokens", usage_data.get("inputTokens"))
    )
    output_tokens = _as_int(
        usage_data.get("output_tokens", usage_data.get("outputTokens"))
    )
    total_tokens = _as_int(
        usage_data.get("total_tokens", usage_data.get("totalTokens"))
    )
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens
    if total_tokens <= 0:
        return None

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return None

    details = usage_data.get("details") if isinstance(usage_data.get("details"), dict) else {}
    metadata_payload = {
        **details,
        **(metadata or {}),
    }

    row = ChatTokenUsage(
        organization_id=project.organization_id,
        project_id=project.id,
        thread_id=None,
        user_id=user_id,
        user_message_id=None,
        assistant_message_id=None,
        provider=usage_data.get("provider"),
        model=usage_data.get("model"),
        source=source,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        is_estimated=bool(
            usage_data.get("is_estimated", usage_data.get("isEstimated", False))
        ),
        metadata_json=json.dumps(metadata_payload) if metadata_payload else None,
    )
    db.add(row)
    db.flush()
    _export_langfuse(row, metadata_payload)
    return row


def estimate_text_token_usage(
    *,
    input_text: str,
    output_text: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    details: Optional[dict] = None,
) -> dict:
    def estimate(text: str) -> int:
        value = str(text or "")
        return max(1, (len(value) + 3) // 4) if value else 0

    input_tokens = estimate(input_text)
    output_tokens = estimate(output_text)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "provider": provider,
        "model": model,
        "is_estimated": True,
        "details": details or {},
    }


def get_thread_token_usage(db: Session, thread_id: str) -> list[ChatTokenUsage]:
    return (
        db.query(ChatTokenUsage)
        .filter(ChatTokenUsage.thread_id == thread_id)
        .order_by(ChatTokenUsage.created_at.asc(), ChatTokenUsage.id.asc())
        .all()
    )


def get_project_token_usage(db: Session, project_id: str) -> list[ChatTokenUsage]:
    return (
        db.query(ChatTokenUsage)
        .filter(ChatTokenUsage.project_id == project_id)
        .order_by(ChatTokenUsage.created_at.asc(), ChatTokenUsage.id.asc())
        .all()
    )


def get_organization_token_totals(db: Session, organization_id: str) -> dict:
    row = (
        db.query(
            func.coalesce(func.sum(ChatTokenUsage.input_tokens), 0),
            func.coalesce(func.sum(ChatTokenUsage.output_tokens), 0),
            func.coalesce(func.sum(ChatTokenUsage.total_tokens), 0),
            func.count(ChatTokenUsage.id),
        )
        .filter(ChatTokenUsage.organization_id == organization_id)
        .one()
    )
    return {
        "input_tokens": _as_int(row[0]),
        "output_tokens": _as_int(row[1]),
        "total_tokens": _as_int(row[2]),
        "entries": _as_int(row[3]),
    }


def _export_langfuse(row: ChatTokenUsage, metadata: dict) -> None:
    if str(os.getenv("CHIPVERIFY_LANGFUSE_ENABLED", "")).strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return

    try:
        from langfuse import get_client

        client = get_client()
        with client.start_as_current_observation(
            as_type="generation",
            name=row.source or "chipverify-chat",
            model=row.model,
            metadata={
                "provider": row.provider,
                "project_id": row.project_id,
                "thread_id": row.thread_id,
                "assistant_message_id": row.assistant_message_id,
                **(metadata or {}),
            },
        ) as generation:
            generation.update(
                usage_details={
                    "input": row.input_tokens,
                    "output": row.output_tokens,
                    "total": row.total_tokens,
                }
            )
    except Exception:
        logger.debug("Langfuse token usage export skipped or failed", exc_info=True)
