"""In-process event hub for codebase graph build progress (WebSocket forwarding)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List

logger = logging.getLogger(__name__)

ProjectEventCallback = Callable[[dict[str, Any]], Awaitable[None]]
SyncProjectEventCallback = Callable[[dict[str, Any]], None]

_subscribers: Dict[str, List[ProjectEventCallback]] = {}
_sync_subscribers: Dict[str, List[SyncProjectEventCallback]] = {}
_main_loop: asyncio.AbstractEventLoop | None = None


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


def subscribe_project(project_id: str, callback: ProjectEventCallback) -> Callable[[], None]:
    """Register an async callback for project-scoped graph events. Returns unsubscribe."""

    pid = str(project_id)
    _subscribers.setdefault(pid, []).append(callback)

    def _unsub() -> None:
        callbacks = _subscribers.get(pid, [])
        if callback in callbacks:
            callbacks.remove(callback)
        if not callbacks:
            _subscribers.pop(pid, None)

    return _unsub


async def emit_project_event(project_id: str, event: dict[str, Any]) -> None:
    """Broadcast an event to all async subscribers for this project."""

    payload = {**event, "project_id": str(project_id)}
    for callback in list(_subscribers.get(str(project_id), [])):
        try:
            await callback(payload)
        except Exception:
            logger.debug("codebase graph event callback failed", exc_info=True)


def emit_project_event_sync(project_id: str, event: dict[str, Any]) -> None:
    """Fire-and-forget emit from background threads."""

    payload = {**event, "project_id": str(project_id)}
    if _main_loop and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(emit_project_event(project_id, event), _main_loop)
        return
    for callback in list(_subscribers.get(str(project_id), [])):
        try:
            if _main_loop and _main_loop.is_running():
                asyncio.run_coroutine_threadsafe(callback(payload), _main_loop)
        except Exception:
            logger.debug("codebase graph sync emit failed", exc_info=True)
