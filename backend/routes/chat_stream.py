"""
Vercel AI SDK–compatible HTTP streaming chat endpoint.
======================================================
Implements the UI Message Stream Protocol so the frontend can use
`useChat` from `@ai-sdk/react` directly.

Protocol reference:
  https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol

Endpoint:
  POST /api/v1/chat/threads/{thread_id}/stream

Request body (JSON):
  {
    "messages": [
      { "role": "user"|"assistant"|"tool", "content": "...", "id": "...", "parts": [...] }
    ],
    "context": {
      "project_id": "...",
      "active_file_name": "...",
      "active_file_content": "...",
      ...
    }
  }

Response:
  Content-Type: text/event-stream
  x-vercel-ai-ui-message-stream: v1

  SSE events as per the Vercel AI SDK data stream protocol.

The agentic tool-execution loop is delegated to the frontend via
`useChat`'s onToolCall / addToolOutput / sendAutomaticallyWhen
pattern.  When the LLM requests tool calls the server streams them
and returns finishReason="tool-calls".  The frontend executes the
tools and resubmits the full message history (including tool-result
parts).  The server resumes generation with the new history.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.database import SessionLocal
from database.models import ChatMessage, ChatThread, ChatThreadState, User
from llm_provider import (
    chat_with_tools_stream,
    ChatResponse,
    ToolCall,
)
from services.token_usage import persist_chat_token_usage

# ── Tool Definitions for the streaming chat endpoint ──────────────────
#
# IMPORTANT: Only include tools that the FRONTEND can actually execute.
# Server-side-only tools (findTodos, mergeTodos, attemptCompletion) must
# NOT be listed here — the LLM would call them but the frontend can't
# execute them, breaking the client-side tool delegation loop.

CHAT_STREAM_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "listFiles",
            "description": (
                "List all files/artifacts in the current project. "
                "Call this FIRST before reading files to discover available artifact IDs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "The project UUID. Use the one from workspace context.",
                    }
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readFile",
            "description": "Read the content of a single file/artifact by its UUID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "The UUID of the artifact to read (from listFiles).",
                    }
                },
                "required": ["artifact_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readFiles",
            "description": (
                "Read multiple files at once in a single tool call. "
                "PREFER this over calling readFile multiple times when you need to read several files. "
                "More efficient for reading 2+ files simultaneously."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of artifact UUIDs to read in parallel (from listFiles).",
                    }
                },
                "required": ["artifact_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readSpecPages",
            "description": (
                "Read raw text from specific pages of the active or specified spec document. "
                "Use the document index in context to pick page ranges. Max 30 pages per call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "artifact_id": {"type": "string"},
                    "start_page": {"type": "integer"},
                    "end_page": {"type": "integer"},
                },
                "required": ["start_page", "end_page"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readSpecSection",
            "description": "Read raw text for a section title from the active spec document index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "artifact_id": {"type": "string"},
                    "section_title": {"type": "string"},
                },
                "required": ["section_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "searchSpec",
            "description": "Search the active spec document's local page/section index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "artifact_id": {"type": "string"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createFile",
            "description": "Create a new file/artifact in the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "filename": {"type": "string"},
                    "artifact_type": {
                        "type": "string",
                        "enum": ["spec", "rtl", "generated"],
                    },
                    "content": {"type": "string"},
                },
                "required": ["project_id", "filename", "artifact_type", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "applyCodeToFile",
            "description": "Apply code changes to an existing file/artifact.",
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "code": {"type": "string"},
                    "strategy": {
                        "type": "string",
                        "enum": ["replace_file", "replace_selection", "smart_insert"],
                        "description": "replace_file: full overwrite. replace_selection: replace old_content. smart_insert: append.",
                    },
                    "old_content": {
                        "type": "string",
                        "description": "Required when strategy is replace_selection.",
                    },
                },
                "required": ["artifact_id", "code", "strategy"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "runSimulation",
            "description": "Run a verification simulation for the project using the active spec and RTL artifacts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                },
                "required": ["project_id"],
            },
        },
    },
]

AGENT_SYSTEM_PROMPT = """\
You are Chip Verify AI, a master hardware verification and design engineer.
You help the user develop SystemVerilog RTL, write UVM testbenches, and debug their code.

You have access to the following tools to interact with the project workspace:
- listFiles: discover files in the project (always call this first)
- readFile: read a single file by artifact_id
- readFiles: read MULTIPLE files at once — PREFER this when reading 2+ files
- readSpecPages: read page-bounded raw text from large specs
- readSpecSection: read a section by title from the spec index
- searchSpec: search the local spec page/section index
- createFile: create a new file
- applyCodeToFile: edit an existing file
- runSimulation: run the verification simulation

Rules:
1. When the user asks you to read or explain multiple files, use readFiles with all IDs in one call.
2. For spec-specific questions, first use the Active Specification Document Index in context. If more detail is needed, call readSpecPages/readSpecSection/searchSpec instead of readFile on a large PDF.
3. Explain what you are doing before and after each tool call.
4. When you have completed the user's request, respond naturally — do not wait for further instruction.
5. Never call the same tool repeatedly with identical arguments in a single turn unless the previous attempt failed.
6. If a recent listFiles result is already available in the conversation, reuse it instead of calling listFiles again.
"""

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat-stream"])

# ── Auth helpers (mirrors api.py) ─────────────────────────────────────

_DEFAULT_DEV_SECRET = "chipverify-local-dev-secret-change-me"
_SECRET_KEY = (os.environ.get("CHIPVERIFY_SECRET_KEY") or "").strip()
_ALGORITHM = "HS256"

oauth2_bearer = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _authenticate_user(token: str, db: Session) -> User:
    """Authenticate user from JWT token or fall back to dev user."""
    if token:
        try:
            payload = jwt.decode(token, _SECRET_KEY, algorithms=[_ALGORITHM])
            user_id: str = payload.get("sub") or ""
            if user_id:
                user = db.query(User).filter(User.id == user_id).first()
                if user:
                    return user
        except JWTError:
            pass

    allow_dev_auth = os.getenv("CHIPVERIFY_ALLOW_DEV_AUTH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if allow_dev_auth:
        dev_user = db.query(User).first()
        if dev_user:
            return dev_user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


# ── Request / Response models ─────────────────────────────────────────


class MessagePart(BaseModel):
    type: str
    text: Optional[str] = None
    toolCallId: Optional[str] = None
    toolName: Optional[str] = None
    input: Optional[Any] = None
    output: Optional[Any] = None
    state: Optional[str] = None
    errorText: Optional[str] = None


class ChatStreamMessage(BaseModel):
    id: Optional[str] = None
    role: str  # "user" | "assistant" | "tool"
    content: Optional[str] = None
    parts: Optional[list[MessagePart]] = None
    # For tool-result messages
    toolCallId: Optional[str] = None
    toolName: Optional[str] = None


class ChatStreamRequest(BaseModel):
    messages: list[ChatStreamMessage]
    context: Optional[dict] = None
    thread_id: Optional[str] = None


# ── Vercel AI SDK Data Stream helpers ─────────────────────────────────


def _sse(data: dict) -> str:
    """Format a single SSE event line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _ping() -> str:
    """Vercel AI SDK keepalive comment — ignored by SDK but keeps connection alive."""
    return ": ping\n\n"


async def _stream_generator(
    messages: list[ChatStreamMessage],
    context: dict,
    db: Session,
    thread_id: str,
    user_id: str,
    user_message_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    Core streaming generator.

    Converts the Vercel AI SDK `messages` array into the native LLM
    provider format, calls the LLM with streaming + tool support,
    and emits Vercel AI SDK Data Stream Protocol events.
    """

    # ── 1. Build LLM message history ─────────────────────────────────
    llm_messages: list[dict] = []

    # Build system prompt with context
    system_content = _build_system_prompt(context)
    llm_messages.append({"role": "system", "content": system_content})

    resolved_tool_call_ids = _collect_resolved_tool_call_ids(messages)

    for msg in messages:
        role = msg.role

        if role == "user":
            # Extract text from parts or content
            text = _extract_text_from_message(msg)
            if text:
                llm_messages.append({"role": "user", "content": text})

        elif role == "assistant":
            # Reconstruct assistant message; may have tool_calls attached
            text = _extract_text_from_message(msg)
            tool_calls_native = _extract_tool_calls_from_message(
                msg,
                resolved_tool_call_ids=resolved_tool_call_ids,
            )
            tool_results_native = _extract_tool_results_from_message(msg)

            if tool_calls_native:
                # Append both text (if any) and tool_calls
                llm_messages.append(
                    {
                        "role": "assistant",
                        "content": text or "",
                        "tool_calls": tool_calls_native,
                    }
                )
            elif text:
                llm_messages.append({"role": "assistant", "content": text})

            # If the assistant message includes resolved tool outputs, forward them
            # as explicit tool result messages so the model can continue reasoning.
            if tool_results_native:
                for tool_result in tool_results_native:
                    tool_call_id = str(tool_result.get("tool_call_id") or "")
                    if tool_call_id:
                        resolved_tool_call_ids.add(tool_call_id)
                llm_messages.extend(tool_results_native)

        elif role == "tool":
            # Tool result - Vercel AI SDK sends these as separate messages
            # with toolCallId and content
            tool_call_id = msg.toolCallId or ""
            tool_name = msg.toolName or ""
            content = _extract_text_from_message(msg)

            # Try to parse output from parts
            output = None
            if msg.parts:
                for part in msg.parts:
                    if part.type.startswith("tool-") and part.output is not None:
                        output = part.output
                        break

            result_content = (
                json.dumps(output, ensure_ascii=False)
                if output is not None
                else (content or "{}")
            )

            llm_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "content": result_content,
                }
            )

    # ── 2. Stream from LLM ────────────────────────────────────────────
    # AI SDK v5 requires a "start" event FIRST so it can associate the
    # message ID with subsequent text-* and tool-* events.
    message_id = f"msg_{uuid.uuid4().hex[:24]}"
    yield _sse({"type": "start", "messageId": message_id})

    text_id = f"text_{uuid.uuid4().hex[:16]}"
    accumulated_text = ""
    accumulated_tool_calls: list[dict[str, Any]] = []
    finish_reason = "stop"
    token_usage = None

    yield _sse({"type": "text-start", "id": text_id})

    # Keepalive task — sends a ping every 15s to prevent proxy timeouts
    keepalive_queue: asyncio.Queue = asyncio.Queue()

    async def _keepalive_sender():
        while True:
            await asyncio.sleep(15)
            await keepalive_queue.put(_ping())

    keepalive_task = asyncio.create_task(_keepalive_sender())

    try:
        async for chunk in chat_with_tools_stream(
            messages=llm_messages,
            tools=CHAT_STREAM_TOOL_DEFINITIONS,
        ):
            chunk: ChatResponse
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage:
                token_usage = chunk_usage

            # Drain any pending keepalive pings
            while not keepalive_queue.empty():
                yield await keepalive_queue.get()

            # Stream text deltas
            if chunk.content:
                delta = chunk.content
                accumulated_text += delta
                yield _sse({"type": "text-delta", "id": text_id, "delta": delta})

            # Capture tool calls
            if chunk.tool_calls:
                for tool_call in chunk.tool_calls:
                    call_id = tool_call.id or f"tc_{uuid.uuid4().hex[:12]}"
                    accumulated_tool_calls.append(
                        {
                            "toolCallId": call_id,
                            "toolName": tool_call.name,
                            "input": tool_call.arguments,
                        }
                    )

            if chunk.finish_reason in ("stop", "tool_calls", "length"):
                finish_reason = (
                    "tool-calls"
                    if chunk.finish_reason == "tool_calls"
                    else chunk.finish_reason
                )

    except asyncio.CancelledError:
        finish_reason = "cancelled"
        logger.info("Chat stream cancelled: thread=%s", thread_id)
    except Exception as exc:
        logger.exception("Chat stream error: thread=%s error=%s", thread_id, str(exc))
        keepalive_task.cancel()
        yield _sse({"type": "text-end", "id": text_id})
        yield _sse(
            {
                "type": "error",
                "error": {
                    "message": f"LLM generation failed: {str(exc)[:200]}",
                    "code": "LLM_ERROR",
                },
            }
        )
        yield _sse(
            {
                "type": "finish",
                "finishReason": "error",
                "usage": {"inputTokens": 0, "outputTokens": 0},
            }
        )
        return
    finally:
        keepalive_task.cancel()

    yield _sse({"type": "text-end", "id": text_id})

    # ── 3. Emit tool-call events ──────────────────────────────────────
    for tc in accumulated_tool_calls:
        call_id = tc["toolCallId"]
        tool_name = tc["toolName"]
        input_data = tc["input"] or {}

        yield _sse(
            {
                "type": "tool-input-start",
                "toolCallId": call_id,
                "toolName": tool_name,
            }
        )
        # Stream the input as a single delta for simplicity
        input_str = json.dumps(input_data, ensure_ascii=False)
        yield _sse(
            {
                "type": "tool-input-delta",
                "toolCallId": call_id,
                "delta": input_str,
            }
        )
        yield _sse(
            {
                "type": "tool-input-available",
                "toolCallId": call_id,
                "toolName": tool_name,
                "input": input_data,
            }
        )

    # ── 4. Finish event ───────────────────────────────────────────────
    yield _sse(
        {
            "type": "finish",
            "finishReason": finish_reason,
            "usage": token_usage.to_ai_sdk_usage()
            if token_usage
            else {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
        }
    )

    # ── 5. Persist message to DB (best-effort) ────────────────────────
    assistant_message_id = None
    if accumulated_text.strip() and thread_id:
        try:
            assistant_message_id = _persist_assistant_message(
                db, thread_id, accumulated_text, user_id
            )
        except Exception as exc:
            logger.warning(
                "Failed to persist assistant message: thread=%s error=%s",
                thread_id,
                str(exc),
            )

    if token_usage and thread_id:
        try:
            persist_chat_token_usage(
                db,
                thread_id=thread_id,
                user_id=user_id,
                usage=token_usage,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                source="chat_stream",
                metadata={"context_keys": sorted((context or {}).keys())},
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning(
                "Failed to persist token usage: thread=%s error=%s",
                thread_id,
                str(exc),
            )


# ── Context / prompt helpers ──────────────────────────────────────────


def _build_system_prompt(context: dict) -> str:
    """Augment base system prompt with runtime workspace context."""
    base = AGENT_SYSTEM_PROMPT

    parts: list[str] = []

    project_id = context.get("project_id")
    if project_id:
        parts.append(f"Project ID: {project_id}")

    project_name = context.get("project_name")
    if project_name:
        parts.append(f"Project name: {project_name}")

    active_file_name = context.get("active_file_name")
    if active_file_name:
        parts.append(f"Active file: {active_file_name}")

    active_file_content = context.get("active_file_content")
    if active_file_content:
        parts.append(
            f"Active file content (excerpt):\n{str(active_file_content)[:8000]}"
        )

    workspace_files = context.get("workspace_files")
    if workspace_files:
        if isinstance(workspace_files, list):
            limited = workspace_files[:20]
            parts.append("Workspace files:\n" + "\n".join(f"  - {f}" for f in limited))

    manifest_summary = context.get("workspace_manifest_summary")
    if isinstance(manifest_summary, str) and manifest_summary.strip():
        parts.append(manifest_summary.strip()[:12000])

    spec_index = context.get("active_spec_document_index")
    if isinstance(spec_index, str) and spec_index.strip():
        parts.append(
            "Active Specification Document Index (textbook-style map; use this first):\n"
            + spec_index.strip()[:12000]
            + "\n\nIf the user asks for details not fully captured here, call "
            "readSpecPages, readSpecSection, or searchSpec. Cite page numbers."
        )

    attached_file_contents = context.get("attached_file_contents")
    if isinstance(attached_file_contents, list) and attached_file_contents:
        snippets = []
        for item in attached_file_contents[:5]:
            if not isinstance(item, dict):
                continue
            fname = str(item.get("filename") or item.get("id") or "file")[:200]
            content = str(item.get("content") or "")[:4000]
            if content:
                snippets.append(f"[{fname}]\n{content}")
        if snippets:
            parts.append("Attached files:\n\n" + "\n\n".join(snippets))

    if not parts:
        return base

    return f"{base}\n\n## Workspace Context\n\n" + "\n\n".join(parts)


def _extract_text_from_message(msg: ChatStreamMessage) -> str:
    """Extract plain text from a message, checking parts first then content."""
    if msg.parts:
        texts = []
        for part in msg.parts:
            if part.type == "text" and part.text:
                texts.append(part.text)
        if texts:
            return "\n".join(texts)
    return msg.content or ""


def _extract_tool_calls_from_message(
    msg: ChatStreamMessage,
    resolved_tool_call_ids: Optional[set[str]] = None,
) -> list[dict]:
    """
    Extract tool call parts and convert to OpenAI-compatible tool_calls format
    for inclusion in an assistant message to the LLM.
    """
    if not msg.parts:
        return []

    resolved_ids = resolved_tool_call_ids or set()
    output_ids = {
        str(part.toolCallId)
        for part in msg.parts
        if part.type.startswith("tool-")
        and part.state in ("output-available", "output-error")
        and part.toolCallId
    }

    tool_calls = []
    for part in msg.parts:
        # Only treat "input available" states as tool calls.
        # output-* states represent resolved results and must not be re-issued.
        if not part.type.startswith("tool-"):
            continue
        if part.state not in ("input-available", "input-streaming"):
            continue

        tool_name = part.type[len("tool-") :]  # strip "tool-" prefix
        call_id = part.toolCallId or f"tc_{uuid.uuid4().hex[:12]}"

        # Guard against loops: once a call has an output/error, do not re-send it
        # as a fresh tool call to the model.
        if call_id in output_ids or call_id in resolved_ids:
            continue

        input_data = part.input or {}

        tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(input_data, ensure_ascii=False),
                },
            }
        )

    return tool_calls


def _collect_resolved_tool_call_ids(messages: list[ChatStreamMessage]) -> set[str]:
    """Collect tool call IDs that already have output/error results in the history."""
    resolved_ids: set[str] = set()
    for msg in messages:
        if not msg.parts:
            continue
        for part in msg.parts:
            if not part.type.startswith("tool-"):
                continue
            if part.state not in ("output-available", "output-error"):
                continue
            if part.toolCallId:
                resolved_ids.add(str(part.toolCallId))
    return resolved_ids


def _extract_tool_results_from_message(msg: ChatStreamMessage) -> list[dict]:
    """
    Extract tool output/error parts from assistant messages and convert them into
    native tool result messages expected by the LLM provider.
    """
    if not msg.parts:
        return []

    tool_results = []
    for part in msg.parts:
        if not part.type.startswith("tool-"):
            continue
        if part.state not in ("output-available", "output-error"):
            continue

        tool_name = part.toolName or part.type[len("tool-") :]
        call_id = part.toolCallId or f"tc_{uuid.uuid4().hex[:12]}"

        if part.state == "output-error":
            payload = {
                "success": False,
                "error": part.errorText or "Tool execution failed",
            }
        else:
            payload = part.output if part.output is not None else {}

        tool_results.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": tool_name,
                "content": json.dumps(payload, ensure_ascii=False),
            }
        )

    return tool_results


# ── DB helpers ────────────────────────────────────────────────────────


def _ensure_thread(db: Session, thread_id: str, user_id: str) -> ChatThread:
    """Return existing thread or raise 404."""
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread not found: {thread_id}",
        )
    return thread


def _get_or_create_dev_thread(db: Session, user_id: str) -> Optional[ChatThread]:
    """Return any existing thread for the dev user.

    We intentionally do NOT auto-create a thread here because ChatThread
    requires a non-nullable project_id FK — without a real project we cannot
    insert a valid row.  The frontend is responsible for creating a thread via
    POST /api/v1/projects/{id}/chat/threads before chatting; this helper is
    only a best-effort safety net for the ephemeral-ID fallback path.
    """
    return (
        db.query(ChatThread)
        .filter(ChatThread.user_id == user_id)
        .order_by(ChatThread.created_at.asc())
        .first()
    )


def _persist_assistant_message(
    db: Session, thread_id: str, content: str, user_id: str
) -> str:
    """Persist assistant message to DB."""
    msg = ChatMessage(
        id=str(uuid.uuid4()),
        thread_id=thread_id,
        role="assistant",
        content=content,
    )
    db.add(msg)
    db.commit()
    return msg.id


def _persist_user_message(
    db: Session, thread_id: str, content: str, user_id: str
) -> str:
    """Persist user message to DB."""
    msg = ChatMessage(
        id=str(uuid.uuid4()),
        thread_id=thread_id,
        role="user",
        content=content,
    )
    db.add(msg)
    db.commit()
    return msg.id


def _auto_title_thread(
    db: Session, thread: ChatThread, user_message: str
) -> Optional[str]:
    """Generate thread title from first user message if still default."""
    if thread.title not in ("Project Chat", "Agent Chat", "New Chat", "", None):
        return None

    words = user_message.strip().split()
    title = " ".join(words[:8])
    if len(words) > 8:
        title += "…"
    title = title[:80] or "Chat"

    thread.title = title
    thread.updated_at = datetime.now(timezone.utc)
    db.add(thread)
    db.commit()
    return title


# ── Endpoint ──────────────────────────────────────────────────────────


@router.post("/chat/stream")
async def chat_stream_endpoint(
    request: Request,
    db: Session = Depends(_get_db),
):
    """
    Vercel AI SDK–compatible streaming chat endpoint.

    Streams responses in the UI Message Stream Protocol so the
    frontend can use `useChat` from `@ai-sdk/react` without any
    custom transport code.
    """
    # ── Auth ──────────────────────────────────────────────────────────
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header else ""

    # Also accept token in query string (fallback)
    if not token:
        token = request.query_params.get("token", "")

    try:
        current_user = _authenticate_user(token, db)
    except HTTPException:
        # Allow unauthenticated in pure dev-mode (dev user fallback already
        # handled inside _authenticate_user, so this path means no user at all)
        return StreamingResponse(
            iter(
                [
                    'data: {"type":"error","error":{"message":"Unauthorized","code":"AUTH"}}\n\n'
                ]
            ),
            status_code=401,
            media_type="text/event-stream",
        )

    # ── Parse body ────────────────────────────────────────────────────
    try:
        body = await request.json()
        stream_req = ChatStreamRequest(**body)
    except Exception as exc:
        return StreamingResponse(
            iter(
                [
                    f'data: {{"type":"error","error":{{"message":"Invalid request: {str(exc)[:200]}","code":"BAD_REQUEST"}}}}\n\n'
                ]
            ),
            status_code=400,
            media_type="text/event-stream",
        )

    # ── Resolve thread ────────────────────────────────────────────────
    # Accept thread_id from body or context. If missing, fall back to the
    # dev user's first thread (or auto-create one) so chat works without
    # explicit thread management.
    raw_thread_id = stream_req.thread_id or (
        stream_req.context and stream_req.context.get("thread_id")
    )

    if isinstance(raw_thread_id, str) and raw_thread_id.strip():
        thread_id = raw_thread_id.strip()
        # For ephemeral/local IDs generated by the frontend fallback, skip DB lookup
        # and use the dev thread instead.
        is_ephemeral = thread_id.startswith("local_")
        if is_ephemeral:
            thread = _get_or_create_dev_thread(db, current_user.id)
            if thread is None:
                return StreamingResponse(
                    iter(['data: {"type":"error","error":{"message":"Could not resolve a chat thread","code":"SERVER_ERROR"}}\n\n']),
                    status_code=500,
                    media_type="text/event-stream",
                )
            thread_id = thread.id
        else:
            try:
                thread = _ensure_thread(db, thread_id, current_user.id)
            except HTTPException as exc:
                return StreamingResponse(
                    iter([f'data: {{"type":"error","error":{{"message":"{exc.detail}","code":"NOT_FOUND"}}}}\n\n']),
                    status_code=exc.status_code,
                    media_type="text/event-stream",
                )
    else:
        # No thread_id at all — fall back to dev user's default thread
        thread = _get_or_create_dev_thread(db, current_user.id)
        if thread is None:
            return StreamingResponse(
                iter(['data: {"type":"error","error":{"message":"thread_id is required","code":"BAD_REQUEST"}}\n\n']),
                status_code=400,
                media_type="text/event-stream",
            )
        thread_id = thread.id

    context = stream_req.context or {}
    context["thread_id"] = thread_id
    context.setdefault("project_id", thread.project_id)

    # ── Auto-title ────────────────────────────────────────────────────
    last_user_msg = ""
    for m in reversed(stream_req.messages):
        if m.role == "user":
            last_user_msg = _extract_text_from_message(m)
            break

    try:
        from services.workspace_context import enrich_project_context

        enrich_project_context(db, context, user_message=last_user_msg)
    except Exception:
        logger.debug("Project context enrichment failed", exc_info=True)

    new_title = _auto_title_thread(db, thread, last_user_msg)

    # ── Persist latest user message ───────────────────────────────────
    # Only persist the very last user message to avoid duplicates
    user_message_id = None
    if last_user_msg:
        try:
            user_message_id = _persist_user_message(
                db, thread_id, last_user_msg, current_user.id
            )
        except Exception:
            logger.warning(
                "Failed to persist user message for thread %s",
                thread_id,
                exc_info=True,
            )

    # ── Build streaming response ──────────────────────────────────────
    async def event_stream():
        # Optionally emit thread title update as a custom metadata event
        # before the main stream starts (consumers may ignore this)
        if new_title:
            yield _sse(
                {
                    "type": "metadata",
                    "key": "thread_title",
                    "value": new_title,
                    "thread_id": thread_id,
                }
            )

        async for chunk in _stream_generator(
            messages=stream_req.messages,
            context=context,
            db=db,
            thread_id=thread_id,
            user_id=current_user.id,
            user_message_id=user_message_id,
        ):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "x-vercel-ai-ui-message-stream": "v1",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
        },
    )
