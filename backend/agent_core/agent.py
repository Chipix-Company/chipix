"""
Agent - High-level stateful wrapper around the low-level agent loop.

Provides state management, message queuing, and event subscription APIs.
"""

from __future__ import annotations
import asyncio
from typing import Any, List, Optional, Callable, Awaitable, Union
import logging

from .types import (
    AgentMessage,
    AgentState,
    AgentContext,
    AgentLoopConfig,
    AgentEvent,
    AgentTool,
    Message,
    ThinkingLevel,
    ToolExecutionMode,
)
from .agent_loop import run_agent_loop, run_agent_loop_continue
from .event_stream import EventStream

logger = logging.getLogger(__name__)

# ============================================================================
# Agent Options and State
# ============================================================================


class AgentOptions(TypedDict, total=False):
    """Options for constructing an Agent."""

    initialState: dict
    convertToLlm: Callable[
        [List[AgentMessage]], Union[List[Message], Awaitable[List[Message]]]
    ]
    transformContext: Callable[[List[AgentMessage]], Awaitable[List[AgentMessage]]]
    streamFn: Callable
    getApiKey: Callable[[str], Union[str, None, Awaitable[Union[str, None]]]]
    onPayload: Callable[[Any], None]
    beforeToolCall: Callable[[Any, Any], Awaitable[Union[dict, None]]]
    afterToolCall: Callable[[Any, Any], Awaitable[Union[dict, None]]]
    steeringMode: str  # "one-at-a-time" | "all"
    followUpMode: str  # "one-at-a-time" | "all"
    sessionId: str
    thinkingBudgets: dict
    transport: str  # "sse" | "websocket" | "auto"
    maxRetryDelayMs: int
    toolExecution: ToolExecutionMode


class MutableAgentState:
    """Internal mutable state with accessor properties."""

    def __init__(self, initial_state: Optional[dict] = None):
        initial = initial_state or {}
        self._system_prompt = initial.get("systemPrompt", "")
        self._model = initial.get("model", None)
        self._thinking_level = initial.get("thinkingLevel", "off")

        # Copy arrays to avoid aliasing
        self._tools: List[AgentTool] = list(initial.get("tools", []))
        self._messages: List[AgentMessage] = list(initial.get("messages", []))

        # Runtime-owned fields (readonly from outside)
        self._is_streaming = False
        self._streaming_message: Optional[AgentMessage] = None
        self._pending_tool_calls: set[str] = set()
        self._error_message: Optional[str] = None

    # Readonly runtime fields
    @property
    def isStreaming(self) -> bool:
        return self._is_streaming

    @property
    def streamingMessage(self) -> Optional[AssistantMessage]:
        if (
            self._streaming_message
            and self._streaming_message.get("role") == "assistant"
        ):
            return self._streaming_message
        return None

    @property
    def pendingToolCalls(self) -> frozenset[str]:
        return frozenset(self._pending_tool_calls)

    @property
    def errorMessage(self) -> Optional[str]:
        return self._error_message

    # Mutable fields
    @property
    def systemPrompt(self) -> str:
        return self._system_prompt

    @systemPrompt.setter
    def systemPrompt(self, value: str) -> None:
        self._system_prompt = value

    @property
    def model(self) -> Any:
        return self._model

    @model.setter
    def model(self, value: Any) -> None:
        self._model = value

    @property
    def thinkingLevel(self) -> ThinkingLevel:
        return self._thinking_level

    @thinkingLevel.setter
    def thinkingLevel(self, value: ThinkingLevel) -> None:
        self._thinking_level = value

    @property
    def tools(self) -> List[AgentTool]:
        return list(self._tools)  # Return copy

    @tools.setter
    def tools(self, value: List[AgentTool]) -> None:
        self._tools = list(value)  # Copy

    @property
    def messages(self) -> List[AgentMessage]:
        return list(self._messages)  # Return copy

    @messages.setter
    def messages(self, value: List[AgentMessage]) -> None:
        self._messages = list(value)  # Copy

    # Internal runtime setters
    def _set_streaming(self, value: bool) -> None:
        self._is_streaming = value

    def _set_streaming_message(self, value: Optional[AgentMessage]) -> None:
        self._streaming_message = value

    def _set_error_message(self, value: Optional[str]) -> None:
        self._error_message = value

    def _add_pending_tool_call(self, tool_call_id: str) -> None:
        self._pending_tool_calls.add(tool_call_id)

    def _remove_pending_tool_call(self, tool_call_id: str) -> None:
        self._pending_tool_calls.discard(tool_call_id)

    def _clear_pending_tool_calls(self) -> None:
        self._pending_tool_calls.clear()


class PendingMessageQueue:
    """Queue for steering or follow-up messages."""

    def __init__(self, mode: str = "one-at-a-time"):
        self.mode = mode
        self._messages: List[AgentMessage] = []

    def enqueue(self, message: AgentMessage) -> None:
        """Add a message to the queue."""
        self._messages.append(message)

    def has_items(self) -> bool:
        """Check if queue has messages."""
        return len(self._messages) > 0

    def drain(self) -> List[AgentMessage]:
        """Drain messages based on mode."""
        if self.mode == "all":
            drained = list(self._messages)
            self._messages = []
            return drained

        # one-at-a-time mode
        if not self._messages:
            return []

        first = self._messages[0]
        self._messages = self._messages[1:]
        return [first]

    def clear(self) -> None:
        """Clear all messages from queue."""
        self._messages = []


# ============================================================================
# Agent Class
# ============================================================================


class Agent:
    """High-level wrapper around the low-level agent loop.

    Provides state management, event subscriptions, message queuing,
    and lifecycle control.
    """

    def __init__(self, options: Optional[AgentOptions] = None):
        options = options or AgentOptions()

        self._state = MutableAgentState(options.get("initialState"))
        self.convertToLlm = options.get("convertToLlm", self._default_convert_to_llm)
        self.transformContext = options.get("transformContext")
        self.streamFn = options.get("streamFn")
        self.getApiKey = options.get("getApiKey")
        self.onPayload = options.get("onPayload")
        self.beforeToolCall = options.get("beforeToolCall")
        self.afterToolCall = options.get("afterToolCall")

        self._steering_queue = PendingMessageQueue(
            options.get("steeringMode", "one-at-a-time")
        )
        self._follow_up_queue = PendingMessageQueue(
            options.get("followUpMode", "one-at-a-time")
        )

        self.sessionId = options.get("sessionId")
        self.thinkingBudgets = options.get("thinkingBudgets")
        self.transport = options.get("transport", "sse")
        self.maxRetryDelayMs = options.get("maxRetryDelayMs")
        self.toolExecution = options.get("toolExecution", "parallel")

        self._listeners: set[Callable] = set()
        self._activeRun: Optional[AgentRun] = None

    @staticmethod
    def _default_convert_to_llm(messages: List[AgentMessage]) -> List[Message]:
        """Default message converter - filters to LLM-compatible messages."""
        return [
            m for m in messages if m.get("role") in ("user", "assistant", "toolResult")
        ]

    # ============================================================================
    # Event Subscription
    # ============================================================================

    def subscribe(
        self,
        listener: Callable[[AgentEvent, asyncio.Event], Union[None, Awaitable[None]]],
    ) -> Callable[[], None]:
        """Subscribe to agent lifecycle events.

        Returns an unsubscribe function.
        """
        self._listeners.add(listener)

        def unsubscribe():
            self._listeners.discard(listener)

        return unsubscribe

    # ============================================================================
    # Properties
    # ============================================================================

    @property
    def state(self) -> AgentState:
        """Current agent state."""
        return {
            "systemPrompt": self._state.systemPrompt,
            "model": self._state.model,
            "thinkingLevel": self._state.thinkingLevel,
            "tools": self._state.tools,
            "messages": self._state.messages,
            "isStreaming": self._state.isStreaming,
            "streamingMessage": self._state.streamingMessage,
            "pendingToolCalls": self._state.pendingToolCalls,
            "errorMessage": self._state.errorMessage,
        }

    @property
    def steeringMode(self) -> str:
        """Queue mode for steering messages."""
        return self._steering_queue.mode

    @steeringMode.setter
    def steeringMode(self, mode: str) -> None:
        """Set steering message queue mode."""
        self._steering_queue.mode = mode

    @property
    def followUpMode(self) -> str:
        """Queue mode for follow-up messages."""
        return self._follow_up_queue.mode

    @followUpMode.setter
    def followUpMode(self, mode: str) -> None:
        """Set follow-up message queue mode."""
        self._follow_up_queue.mode = mode

    # ============================================================================
    # Queue Operations
    # ============================================================================

    def steer(self, message: AgentMessage) -> None:
        """Queue message for injection after current turn."""
        self._steering_queue.enqueue(message)

    def followUp(self, message: AgentMessage) -> None:
        """Queue message to run after agent would stop."""
        self._follow_up_queue.enqueue(message)

    def clearSteeringQueue(self) -> None:
        """Remove all queued steering messages."""
        self._steering_queue.clear()

    def clearFollowUpQueue(self) -> None:
        """Remove all queued follow-up messages."""
        self._follow_up_queue.clear()

    def clearAllQueues(self) -> None:
        """Clear both steering and follow-up queues."""
        self.clearSteeringQueue()
        self.clearFollowUpQueue()

    def hasQueuedMessages(self) -> bool:
        """Check if any queues have pending messages."""
        return self._steering_queue.has_items() or self._follow_up_queue.has_items()

    # ============================================================================
    # Control
    # ============================================================================

    @property
    def signal(self) -> Optional[asyncio.Event]:
        """Active abort signal for current run, if any."""
        return self._activeRun.abortSignal if self._activeRun else None

    def abort(self) -> None:
        """Abort the current run if active."""
        if self._activeRun:
            self._activeRun.abort()

    async def waitForIdle(self) -> None:
        """Wait for current run and all listeners to complete."""
        if self._activeRun:
            await self._activeRun.promise

    def reset(self) -> None:
        """Clear transcript, runtime state, and queued messages."""
        self._state.messages = []
        self._state._set_streaming(False)
        self._state._set_streaming_message(None)
        self._state._clear_pending_tool_calls()
        self._state._set_error_message(None)
        self.clearFollowUpQueue()
        self.clearSteeringQueue()

    # ============================================================================
    # Prompting
    # ============================================================================

    async def prompt(self, *args, images=None) -> None:
        """Send a prompt to the agent.

        Supports multiple signatures:
        - prompt(message: AgentMessage | List[AgentMessage])
        - prompt(input: str, images: Optional[List[ImageContent]])
        """
        if self._activeRun:
            raise RuntimeError(
                "Agent is already processing. Use steer() or followUp() "
                "to queue messages, or wait for completion."
            )

        # Parse arguments
        messages = self._normalize_prompt_input(args[0] if args else "", images)
        await self._run_prompt_messages(messages)

    async def continueRun(self) -> None:
        """Continue from current transcript."""
        if self._activeRun:
            raise RuntimeError(
                "Agent is already processing. Wait for completion before continuing."
            )

        messages = self._state.messages
        if not messages:
            raise RuntimeError("No messages to continue from")

        last_message = messages[-1]
        if last_message.get("role") == "assistant":
            # Check steering queue
            steering = self._steering_queue.drain()
            if steering:
                await self._run_prompt_messages(
                    steering, skip_initial_steering_poll=True
                )
                return

            # Check follow-up queue
            follow_ups = self._follow_up_queue.drain()
            if follow_ups:
                await self._run_prompt_messages(follow_ups)
                return

            raise RuntimeError("Cannot continue from assistant message")

        await self._run_continuation()

    def _normalize_prompt_input(
        self, input_arg: Union[str, AgentMessage, List[AgentMessage]], images=None
    ) -> List[AgentMessage]:
        """Normalize various input formats to AgentMessage list."""
        if isinstance(input_arg, list):
            return input_arg

        if not isinstance(input_arg, str):
            return [input_arg]

        # String input
        content = [{"type": "text", "text": input_arg}]
        if images:
            content.extend(images)

        return [{"role": "user", "content": content, "timestamp": 0}]

    async def _run_prompt_messages(
        self, messages: List[AgentMessage], skip_initial_steering_poll=False
    ) -> None:
        """Run agent loop with prompt messages."""
        await self._run_with_lifecycle(
            lambda signal: self._execute_prompt(
                messages, signal, skip_initial_steering_poll
            )
        )

    async def _execute_prompt(
        self,
        messages: List[AgentMessage],
        signal: asyncio.Event,
        skip_initial_steering_poll=False,
    ) -> None:
        """Execute prompt with the low-level agent loop."""

        def emitter(event: AgentEvent) -> None:
            self._process_event(event)

        await run_agent_loop(
            messages,
            self._create_context_snapshot(),
            self._create_loop_config(skip_initial_steering_poll),
            signal,
            self.streamFn,
        )

    async def _run_continuation(self) -> None:
        """Run continuation variant."""
        await self._run_with_lifecycle(
            lambda signal: self._execute_continuation(signal)
        )

    async def _execute_continuation(self, signal: asyncio.Event) -> None:
        """Execute continuation with the low-level agent loop."""
        await run_agent_loop_continue(
            self._create_context_snapshot(),
            self._create_loop_config(),
            signal,
            self.streamFn,
        )

    def _create_context_snapshot(self) -> AgentContext:
        """Create a context snapshot for the current state."""
        return {
            "systemPrompt": self._state.systemPrompt,
            "messages": self._state._messages.copy(),  # Copy for isolation
            "tools": self._state._tools.copy(),
        }

    def _create_loop_config(self, skip_initial_steering_poll=False) -> AgentLoopConfig:
        """Create AgentLoopConfig from current settings."""
        import inspect

        config = AgentLoopConfig(
            model=self._state.model, convertToLlm=self.convertToLlm
        )

        if self.transformContext:
            config["transformContext"] = self.transformContext

        if self.streamFn:
            config["streamFn"] = self.streamFn

        if self.getApiKey:
            config["getApiKey"] = self.getApiKey

        if self.onPayload:
            config["onPayload"] = self.onPayload

        if self.beforeToolCall:
            config["beforeToolCall"] = self.beforeToolCall

        if self.afterToolCall:
            config["afterToolCall"] = self.afterToolCall

        if self.sessionId:
            config["sessionId"] = self.sessionId

        if self.thinkingBudgets:
            config["thinkingBudgets"] = self.thinkingBudgets

        if self.transport:
            config["transport"] = self.transport

        if self.maxRetryDelayMs:
            config["maxRetryDelayMs"] = self.maxRetryDelayMs

        config["toolExecution"] = self.toolExecution

        # Add queue accessors
        def get_steering():
            if skip_initial_steering_poll:
                return []
            return self._steering_queue.drain()

        config["getSteeringMessages"] = get_steering
        config["getFollowUpMessages"] = lambda: self._follow_up_queue.drain()

        return config

    async def _run_with_lifecycle(
        self, executor: Callable[[asyncio.Event], Awaitable[None]]
    ) -> None:
        """Run with lifecycle and state management."""
        if self._activeRun:
            raise RuntimeError("Agent is already processing")

        # Create abort controller
        abort_signal = asyncio.Event()

        # Set up run
        run = AgentRun(abort_signal, executor)
        self._activeRun = run

        # Set runtime state
        self._state._set_streaming(True)
        self._state._set_streaming_message(None)
        self._state._set_error_message(None)

        try:
            await executor(abort_signal)
        except Exception as e:
            await self._handle_run_failure(e, abort_signal.is_set())
        finally:
            self._finish_run()

    async def _handle_run_failure(self, error: Exception, aborted: bool) -> None:
        """Handle run failure with error message."""
        failure_message = {
            "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "stopReason": "aborted" if aborted else "error",
            "api": self._state.model.id if self._state.model else "unknown",
            "provider": self._state.model.provider if self._state.model else "unknown",
            "model": self._state.model.id if self._state.model else "unknown",
            "usage": {},  # EMPTY_USAGE equivalent
            "timestamp": 0,
        }

        self._state._messages.append(failure_message)
        self._state._set_error_message(str(error))
        await self._emit_event({"type": "agent_end", "messages": [failure_message]})

    def _finish_run(self) -> None:
        """Clear runtime state after run completion."""
        self._state._set_streaming(False)
        self._state._set_streaming_message(None)
        self._state._clear_pending_tool_calls()
        if self._activeRun:
            self._activeRun.resolve()
        self._activeRun = None

    def _process_event(self, event: AgentEvent) -> None:
        """Process internal state updates from loop events."""
        event_type = event.get("type")

        if event_type == "message_start":
            self._state._set_streaming_message(event.get("message"))
        elif event_type == "message_update":
            self._state._set_streaming_message(event.get("message"))
        elif event_type == "message_end":
            self._state._set_streaming_message(None)
            message = event.get("message")
            if message:
                self._state._messages.append(message)
        elif event_type == "tool_execution_start":
            if event.get("toolCallId"):
                self._state._add_pending_tool_call(event["toolCallId"])
        elif event_type == "tool_execution_end":
            if event.get("toolCallId"):
                self._state._remove_pending_tool_call(event["toolCallId"])
        elif event_type == "turn_end":
            message = event.get("message", {})
            if message.get("role") == "assistant" and message.get("errorMessage"):
                self._state._set_error_message(message["errorMessage"])
        elif event_type == "agent_end":
            self._state._set_streaming_message(None)

        # Forward to subscribers
        if self._activeRun:
            signal = self._activeRun.abortSignal
            for listener in self._listeners:
                result = listener(event, signal)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)

    async def _emit_event(self, event: AgentEvent) -> None:
        """Emit event to subscribers."""
        for listener in self._listeners:
            result = listener(event, self.signal)
            if asyncio.iscoroutine(result):
                await result


# ============================================================================
# Agent Run Management
# ============================================================================


class AgentRun:
    """Active agent run with promise and abort control."""

    def __init__(
        self,
        abort_signal: asyncio.Event,
        executor: Callable[[asyncio.Event], Awaitable[None]],
    ):
        self.abortSignal = abort_signal
        self.promise: asyncio.Task = asyncio.create_task(executor(abort_signal))

    def abort(self) -> None:
        """Abort this run."""
        self.abortSignal.set()

    async def resolve(self) -> None:
        """Wait for promise to resolve."""
        try:
            await self.promise
        except Exception:
            pass  # Errors handled in _handle_run_failure


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    "Agent",
    "AgentOptions",
    "AgentRun",
    "PendingMessageQueue",
    "MutableAgentState",
]
