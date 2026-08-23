"""
Event Stream - Python async generator wrapper for agent events.

Provides a streaming interface similar to TypeScript EventStream.
"""

from __future__ import annotations
from typing import AsyncGenerator, Any, TypeVar, Optional, AsyncIterable
import asyncio

T = TypeVar('T')
U = TypeVar('U')

class EventStream:
    """Async generator wrapper that provides result() method.
    
    Wraps an async generator with a final result value that can be
    awaited after iteration completes.
    """
    
    def __init__(self, gen: AsyncGenerator[T, None], final_result: Optional[U] = None):
        self._gen = gen
        self._final_result = final_result
        self._completed = False
        self._result_val: Optional[U] = None
    
    def __aiter__(self):
        return self._gen
    
    def __iter__(self):  # For sync iteration if needed
        raise TypeError("EventStream is async iterable. Use 'async for' instead of 'for'.")
    
    async def result(self) -> Optional[U]:
        """Get the final result after iteration completes."""
        if not self._completed:
            # Consume all events to get to final result
            async for _ in self:
                pass
        return self._result_val
    
    async def __anext__(self):
        try:
            val = await self._gen.__anext__()
            return val
        except StopAsyncIteration as e:
            self._completed = True
            if hasattr(e, 'value'):
                self._result_val = e.value
            raise

class EventStreamAdapter:
    """Adapter to create event streams from async generators."""
    
    @staticmethod
    def create(
        gen_fn,
        is_complete_fn=None,
        get_result_fn=None
    ) -> EventStream:
        """Create an event stream from a generator function.
        
        Args:
            gen_fn: Async generator function yielding events
            is_complete_fn: Function to check if event signals completion
            get_result_fn: Function to extract result from complete event
        """
        args = {}
        if is_complete_fn:
            args['is_complete_fn'] = is_complete_fn
        if get_result_fn:
            args['get_result_fn'] = get_result_fn
            
        return EventStream(gen_fn())

async def collect_events(stream: EventStream) -> tuple[list[Any], Optional[Any]]:
    """Collect all events from a stream and return (events, final_result)."""
    events = []
    async for event in stream:
        events.append(event)
    result = await stream.result()
    return events, result

# ============================================================================
# Example usage decorator for creating event streams
# ============================================================================

def event_stream():
    """Decorator to create event streams from async generators."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            return EventStream(func(*args, **kwargs))
        return wrapper
    return decorator
