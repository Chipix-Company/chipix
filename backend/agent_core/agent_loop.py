"""
Agent Loop - Core event-driven agent runtime.

Implementation of @mariozechner/pi-agent-core patterns in Python.
Works with AgentMessage throughout, transforming to LLM format at the boundary.
"""

from __future__ import annotations
import asyncio
from typing import Any, List, Optional, Callable, Awaitable, TypedDict
import json
import logging

from .types import (
    AgentMessage,
    AssistantMessage,
    ToolResultMessage,
    AgentContext,
    AgentLoopConfig,
    AgentEvent,
    ToolCall,
    AgentToolResult,
    BeforeToolCallResult,
    AfterToolCallResult,
    ToolExecutionMode,
)
from .event_stream import EventStream, collect_events

logger = logging.getLogger(__name__)

# ============================================================================
# Main Loop Functions
# ============================================================================


async def agent_loop(
    prompts: List[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Optional[asyncio.Event] = None,
    stream_fn: Optional[Callable] = None,
) -> EventStream:
    """Start an agent loop with a new prompt message.

    The prompt is added to the context and events are emitted for it.

    Returns an EventStream that yields AgentEvent objects and resolves to
    List[AgentMessage] when complete.
    """

    async def run_loop() -> AsyncGenerator[AgentEvent, None]:
        new_messages: List[AgentMessage] = []

        # Initial promise for the result
        async def execute() -> List[AgentMessage]:
            return await run_agent_loop(prompts, context, config, signal, stream_fn)

        # Run the loop and collect events
        messages = await execute()
        new_messages = messages

        # Yield any events from the execution
        # (In a real implementation, we'd need to wire up event emission)

        return new_messages

    return EventStream(run_loop(), None)


async def agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Optional[asyncio.Event] = None,
    stream_fn: Optional[Callable] = None,
) -> EventStream:
    """Continue an agent loop from the current context without adding a new message.

    Used for retries - context already has user message or tool results.

    IMPORTANT: The last message in context must convert to a 'user' or 'toolResult'
    message via convertToLlm. If it doesn't, the LLM provider will reject the request.
    This cannot be validated here since convertToLlm is only called once per turn.
    """
    if not context.get("messages"):
        raise ValueError("Cannot continue: no messages in context")

    last_message = context["messages"][-1]
    if last_message.get("role") == "assistant":
        raise ValueError("Cannot continue from message role: assistant")

    async def run_loop() -> AsyncGenerator[AgentEvent, None]:
        messages = await run_agent_loop_continue(context, config, signal, stream_fn)
        return messages

    return EventStream(run_loop(), None)


async def run_agent_loop(
    prompts: List[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Optional[asyncio.Event] = None,
    stream_fn: Optional[Callable] = None,
) -> List[AgentMessage]:
    """Internal: Run agent loop with new prompts."""
    new_messages: List[AgentMessage] = list(prompts)
    current_context: AgentContext = {
        "systemPrompt": context["systemPrompt"],
        "messages": context["messages"] + prompts,
        "tools": context.get("tools", []),
    }

    async def emit(event: AgentEvent) -> None:
        logger.debug(f"Agent event: {event.get('type')}")

    await emit({"type": "agent_start"})
    await emit({"type": "turn_start"})

    for prompt in prompts:
        await emit({"type": "message_start", "message": prompt})
        await emit({"type": "message_end", "message": prompt})

    await _run_loop(current_context, new_messages, config, signal, emit, stream_fn)

    return new_messages


async def run_agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Optional[asyncio.Event] = None,
    stream_fn: Optional[Callable] = None,
) -> List[AgentMessage]:
    """Internal: Run agent loop from existing context."""
    if not context.get("messages"):
        raise ValueError("Cannot continue: no messages in context")

    last_message = context["messages"][-1]
    if last_message.get("role") == "assistant":
        raise ValueError("Cannot continue from message role: assistant")

    new_messages: List[AgentMessage] = []
    current_context: AgentContext = {
        "systemPrompt": context["systemPrompt"],
        "messages": list(context["messages"]),
        "tools": list(context.get("tools", [])),
    }

    async def emit(event: AgentEvent) -> None:
        logger.debug(f"Agent event: {event.get('type')}")

    await emit({"type": "agent_start"})
    await emit({"type": "turn_start"})

    await _run_loop(current_context, new_messages, config, signal, emit, stream_fn)

    return new_messages


# ============================================================================
# Loop Logic
# ============================================================================


async def _run_loop(
    current_context: AgentContext,
    new_messages: List[AgentMessage],
    config: AgentLoopConfig,
    signal: Optional[asyncio.Event],
    emit: Callable[[AgentEvent], Awaitable[None]],
    stream_fn: Optional[Callable],
) -> None:
    """Main loop logic shared by agent_loop and agent_loop_continue."""
    first_turn = True

    # Check for steering messages at start
    pending_messages: List[AgentMessage] = []
    if config.get("getSteeringMessages"):
        steering = config["getSteeringMessages"]()
        if asyncio.iscoroutine(steering):
            steering = await steering
        pending_messages = steering

    # Outer loop: continues when queued follow-up messages arrive
    while True:
        has_more_tool_calls = True
        pending_messages = list(pending_messages)

        # Inner loop: process tool calls and steering messages
        while has_more_tool_calls or pending_messages:
            if not first_turn:
                await emit({"type": "turn_start"})
            else:
                first_turn = False

            # Process pending messages (inject before next assistant response)
            if pending_messages:
                for message in pending_messages:
                    await emit({"type": "message_start", "message": message})
                    await emit({"type": "message_end", "message": message})
                    current_context["messages"].append(message)
                    new_messages.append(message)
                pending_messages = []

            # Stream assistant response
            message = await _stream_assistant_response(
                current_context, config, signal, emit, stream_fn
            )
            new_messages.append(message)

            if message.get("stopReason") in ("error", "aborted"):
                await emit({"type": "turn_end", "message": message, "toolResults": []})
                await emit({"type": "agent_end", "messages": new_messages})
                return

            # Check for tool calls
            tool_calls = [
                c
                for c in message.get("content", [])
                if isinstance(c, dict) and c.get("type") == "toolCall"
            ]
            has_more_tool_calls = bool(tool_calls)

            tool_results: List[ToolResultMessage] = []
            if has_more_tool_calls:
                tool_results = await _execute_tool_calls(
                    current_context, message, config, signal, emit
                )

                for result in tool_results:
                    current_context["messages"].append(result)
                    new_messages.append(result)

            await emit(
                {"type": "turn_end", "message": message, "toolResults": tool_results}
            )

            # Check for steering messages again
            if config.get("getSteeringMessages"):
                steering = config["getSteeringMessages"]()
                if asyncio.iscoroutine(steering):
                    steering = await steering
                pending_messages = list(steering)

        # Agent would stop here. Check for follow-up messages.
        follow_up_messages: List[AgentMessage] = []
        if config.get("getFollowUpMessages"):
            follow_up = config["getFollowUpMessages"]()
            if asyncio.iscoroutine(follow_up):
                follow_up = await follow_up
            follow_up_messages = list(follow_up)

        if follow_up_messages:
            # Set as pending so inner loop processes them
            pending_messages = follow_up_messages
            continue

        # No more messages, exit
        break

    await emit({"type": "agent_end", "messages": new_messages})


# ============================================================================
# Assistant Response Streaming
# ============================================================================


async def _stream_assistant_response(
    current_context: AgentContext,
    config: AgentLoopConfig,
    signal: Optional[asyncio.Event],
    emit: Callable[[AgentEvent], Awaitable[None]],
    stream_fn: Optional[Callable],
) -> AssistantMessage:
    """Stream assistant response from LLM."""
    # Apply context transform if configured (AgentMessage[] → AgentMessage[])
    messages = list(current_context["messages"])
    if config.get("transformContext"):
        transformed = config["transformContext"](messages, signal)
        if asyncio.iscoroutine(transformed):
            transformed = await transformed
        messages = transformed

    # Convert to LLM-compatible messages (AgentMessage[] → Message[])
    converted = config["convertToLlm"](messages)
    if asyncio.iscoroutine(converted):
        converted = await converted

    # Build LLM context
    llm_context = {
        "systemPrompt": current_context["systemPrompt"],
        "messages": converted,
        "tools": current_context.get("tools", []),
    }

    # Use configured stream function or default
    stream_function = stream_fn

    # Resolve API key if dynamic resolution is configured
    api_key: Optional[str] = config.get("apiKey")
    if config.get("getApiKey"):
        resolved = config["getApiKey"](config.get("model", {}).get("provider", ""))
        if asyncio.iscoroutine(resolved):
            resolved = await resolved
        api_key = resolved or api_key

    if stream_function:
        # Call the stream function
        options = {**config, "apiKey": api_key, "signal": signal}
        response = await stream_function(config.get("model"), llm_context, options)
    else:
        # No streaming function provided, return a default message
        response = {
            "role": "assistant",
            "content": [{"type": "text", "text": "No stream function provided"}],
            "stopReason": "stop",
            "api": "unknown",
            "provider": "unknown",
            "model": "unknown",
            "usage": {},
            "timestamp": 0,
        }

    # Handle the response (simplified for Python implementation)
    if isinstance(response, dict):
        # Return the final message
        final_message = await _emit_message(response, emit, current_context)
        return final_message

    # For Streaming cases (async generators)
    if hasattr(response, "__aiter__"):
        partial_message = None
        added_partial = False

        async for event in response:
            if event.get("type") == "start":
                partial_message = event.get("partial")
                current_context["messages"].append(partial_message)
                added_partial = True
                await emit({"type": "message_start", "message": {**partial_message}})
            elif event.get("type") in [
                "text_start",
                "text_delta",
                "text_end",
                "thinking_start",
                "thinking_delta",
                "thinking_end",
                "toolcall_start",
                "toolcall_delta",
                "toolcall_end",
            ]:
                if partial_message:
                    partial_message = event.get("partial")
                    current_context["messages"][-1] = partial_message
                    await emit(
                        {
                            "type": "message_update",
                            "assistantMessageEvent": event,
                            "message": {**partial_message},
                        }
                    )

        # Get final message
        final_message = await _get_result(response)
        if added_partial:
            current_context["messages"][-1] = final_message
        else:
            current_context["messages"].append(final_message)
            await emit({"type": "message_start", "message": {**final_message}})

        await emit({"type": "message_end", "message": final_message})

        return final_message

    # Fallback for other response types
    final_message = {
        "role": "assistant",
        "content": [{"type": "text", "text": str(response)}],
        "stopReason": "stop",
        "api": "unknown",
        "provider": "unknown",
        "model": "unknown",
        "usage": {},
        "timestamp": 0,
    }

    return await _emit_message(final_message, emit, current_context)


async def _emit_message(
    message: AssistantMessage,
    emit: Callable[[AgentEvent], Awaitable[None]],
    context: AgentContext,
) -> AssistantMessage:
    """Emit message start/end events."""
    await emit({"type": "message_start", "message": {**message}})
    await emit({"type": "message_end", "message": message})
    return message


async def _get_result(stream) -> dict:
    """Get final result from stream."""
    if hasattr(stream, "result"):
        result = stream.result()
        if asyncio.iscoroutine(result):
            result = await result
        return result
    return {}


# ============================================================================
# Tool Execution
# ============================================================================


async def _execute_tool_calls(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    signal: Optional[asyncio.Event],
    emit: Callable[[AgentEvent], Awaitable[None]],
) -> List[ToolResultMessage]:
    """Execute tool calls from an assistant message."""
    tool_execution = config.get("toolExecution", "parallel")

    if tool_execution == "sequential":
        return await _execute_tool_calls_sequential(
            current_context, assistant_message, config, signal, emit
        )
    else:
        return await _execute_tool_calls_parallel(
            current_context, assistant_message, config, signal, emit
        )


async def _execute_tool_calls_sequential(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    signal: Optional[asyncio.Event],
    emit: Callable[[AgentEvent], Awaitable[None]],
) -> List[ToolResultMessage]:
    """Execute tool calls one by one."""
    results: List[ToolResultMessage] = []

    tool_calls = [
        c
        for c in assistant_message.get("content", [])
        if isinstance(c, dict) and c.get("type") == "toolCall"
    ]

    for tool_call in tool_calls:
        await emit(
            {
                "type": "tool_execution_start",
                "toolCallId": tool_call["id"],
                "toolName": tool_call["name"],
                "args": tool_call["arguments"],
            }
        )

        preparation = await _prepare_tool_call(
            current_context, assistant_message, tool_call, config, signal
        )

        if preparation["kind"] == "immediate":
            result = await _emit_tool_outcome(
                tool_call, preparation["result"], preparation["isError"], emit
            )
            results.append(result)
        else:
            executed = await _execute_prepared_tool(preparation, signal, emit)
            result = await _finalize_executed_tool(
                current_context,
                assistant_message,
                preparation,
                executed,
                config,
                signal,
                emit,
            )
            results.append(result)

    return results


async def _execute_tool_calls_parallel(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    signal: Optional[asyncio.Event],
    emit: Callable[[AgentEvent], Awaitable[None]],
) -> List[ToolResultMessage]:
    """Execute tool calls concurrently with sequential preflight."""
    results: List[ToolResultMessage] = []
    runnable_calls = []

    tool_calls = [
        c
        for c in assistant_message.get("content", [])
        if isinstance(c, dict) and c.get("type") == "toolCall"
    ]

    for tool_call in tool_calls:
        await emit(
            {
                "type": "tool_execution_start",
                "toolCallId": tool_call["id"],
                "toolName": tool_call["name"],
                "args": tool_call["arguments"],
            }
        )

        preparation = await _prepare_tool_call(
            current_context, assistant_message, tool_call, config, signal
        )

        if preparation["kind"] == "immediate":
            result = await _emit_tool_outcome(
                tool_call, preparation["result"], preparation["isError"], emit
            )
            results.append(result)
        else:
            runnable_calls.append(preparation)

    # Execute runnable calls concurrently
    running_calls = []
    for prepared in runnable_calls:

        async def run_maker(p):
            return await _execute_prepared_tool(p, signal, emit)

        running_calls.append((prepared, asyncio.create_task(run_maker(prepared))))

    # Await and finalize in assistant source order
    for prepared, execution in running_calls:
        executed = await execution
        result = await _finalize_executed_tool(
            current_context, assistant_message, prepared, executed, config, signal, emit
        )
        results.append(result)

    return results


class _PreparedToolCall(TypedDict):
    """Prepared tool call ready for execution."""

    kind: str  # "prepared"
    toolCall: ToolCall
    tool: Dict[str, Any]  # AgentTool (typing simplified)
    args: Any


class _ImmediateToolCallOutcome(TypedDict):
    """Immediate tool call outcome (blocked or error)."""

    kind: str  # "immediate"
    result: AgentToolResult
    isError: bool


class _ExecutedToolCallOutcome(TypedDict):
    """Executed tool call outcome."""

    result: AgentToolResult
    isError: bool


async def _prepare_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_call: ToolCall,
    config: AgentLoopConfig,
    signal: Optional[asyncio.Event],
) -> Union[_PreparedToolCall, _ImmediateToolCallOutcome]:
    """Prepare a tool call for execution."""
    tools = current_context.get("tools", [])
    tool = next((t for t in tools if t.get("name") == tool_call["name"]), None)

    if not tool:
        return {
            "kind": "immediate",
            "result": _create_error_tool_result(f"Tool {tool_call['name']} not found"),
            "isError": True,
        }

    try:
        # Validate arguments
        args = tool_call["arguments"]  # In real implementation, do schema validation

        # Call beforeToolCall hook
        if config.get("beforeToolCall"):
            before_ctx = {
                "assistantMessage": assistant_message,
                "toolCall": tool_call,
                "args": args,
                "context": current_context,
            }
            before_result = config["beforeToolCall"](before_ctx, signal)
            if asyncio.iscoroutine(before_result):
                before_result = await before_result

            if before_result and before_result.get("block"):
                reason = before_result.get("reason", "Tool execution was blocked")
                return {
                    "kind": "immediate",
                    "result": _create_error_tool_result(reason),
                    "isError": True,
                }

        return {"kind": "prepared", "toolCall": tool_call, "tool": tool, "args": args}
    except Exception as e:
        return {
            "kind": "immediate",
            "result": _create_error_tool_result(str(e)),
            "isError": True,
        }


async def _execute_prepared_tool(
    prepared: _PreparedToolCall,
    signal: Optional[asyncio.Event],
    emit: Callable[[AgentEvent], Awaitable[None]],
) -> _ExecutedToolCallOutcome:
    """Execute a prepared tool call."""
    update_events = []

    # Tool update callback
    def on_update(partial_result):
        async def emit_update():
            await emit(
                {
                    "type": "tool_execution_update",
                    "toolCallId": prepared["toolCall"]["id"],
                    "toolName": prepared["toolCall"]["name"],
                    "args": prepared["toolCall"]["arguments"],
                    "partialResult": partial_result,
                }
            )

        update_events.append(asyncio.create_task(emit_update()))

    try:
        # Execute the tool
        result = await prepared["tool"]["execute"](
            prepared["toolCall"]["id"], prepared["args"], signal, on_update
        )

        # Wait for all update events
        if update_events:
            await asyncio.gather(*update_events, return_exceptions=True)

        return {"result": result, "isError": False}
    except Exception as e:
        # Wait for update events even on error
        if update_events:
            await asyncio.gather(*update_events, return_exceptions=True)

        return {"result": _create_error_tool_result(str(e)), "isError": True}


async def _finalize_executed_tool(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    prepared: _PreparedToolCall,
    executed: _ExecutedToolCallOutcome,
    config: AgentLoopConfig,
    signal: Optional[asyncio.Event],
    emit: Callable[[AgentEvent], Awaitable[None]],
) -> ToolResultMessage:
    """Finalize tool call after execution and call afterToolCall hook."""
    result = executed["result"]
    is_error = executed["isError"]

    # Call afterToolCall hook to mutate results if needed
    if config.get("afterToolCall"):
        after_ctx = {
            "assistantMessage": assistant_message,
            "toolCall": prepared["toolCall"],
            "args": prepared["args"],
            "result": result,
            "isError": is_error,
            "context": current_context,
        }
        after_result = config["afterToolCall"](after_ctx, signal)
        if asyncio.iscoroutine(after_result):
            after_result = await after_result

        if after_result:
            result = {
                "content": after_result.get("content", result.get("content", [])),
                "details": after_result.get("details", result.get("details", {})),
            }
            is_error = after_result.get("isError", is_error)

    return await _emit_tool_outcome(prepared["toolCall"], result, is_error, emit)


def _create_error_tool_result(message: str) -> AgentToolResult:
    """Create an error tool result."""
    return {"content": [{"type": "text", "text": message}], "details": {}}


async def _emit_tool_outcome(
    tool_call: ToolCall,
    result: AgentToolResult,
    is_error: bool,
    emit: Callable[[AgentEvent], Awaitable[None]],
) -> ToolResultMessage:
    """Emit tool execution events and create result message."""
    await emit(
        {
            "type": "tool_execution_end",
            "toolCallId": tool_call["id"],
            "toolName": tool_call["name"],
            "result": result,
            "isError": is_error,
        }
    )

    tool_result_message: ToolResultMessage = {
        "role": "toolResult",
        "toolCallId": tool_call["id"],
        "toolName": tool_call["name"],
        "content": result["content"],
        "details": result.get("details", {}),
        "isError": is_error,
        "timestamp": 0,  # Will be set
    }

    await emit({"type": "message_start", "message": tool_result_message})
    await emit({"type": "message_end", "message": tool_result_message})

    return tool_result_message


# ============================================================================
# Event Collection Helper (for testing/debugging)
# ============================================================================


async def collect_agent_events(
    stream: EventStream,
) -> tuple[list[AgentEvent], Optional[list[AgentMessage]]]:
    """Collect all events from an agent stream and return (events, messages)."""
    events = []
    async for event in stream:
        events.append(event)

    messages = await stream.result()
    return events, messages
