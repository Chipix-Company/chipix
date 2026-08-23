"""
Agent Core Types - Python equivalents of TypeScript reference architecture.

This module provides type definitions for building event-driven agent loops
following the patterns from @mariozechner/pi-agent-core.
"""

from __future__ import annotations
from typing import (
    TypedDict,
    Protocol,
    Any,
    Union,
    List,
    Dict,
    Optional,
    Callable,
    Awaitable,
    runtime_checkable,
)
from datetime import datetime
from typing_extensions import NotRequired, TypeAlias

# ============================================================================
# Message Types - Inspired by TypeScript Message union types
# ============================================================================


class TextContent(TypedDict):
    """Text content block."""

    type: str  # "text"
    text: str


class ImageContent(TypedDict):
    """Image content block."""

    type: str  # "image"
    data: str  # base64 data
    mimeType: str


ContentItem = Union[TextContent, ImageContent]


class UserMessage(TypedDict):
    """User message."""

    role: str  # "user"
    content: Union[str, List[ContentItem]]
    timestamp: float


class AssistantMessage(TypedDict):
    """Assistant message."""

    role: str  # "assistant"
    content: List[ContentItem]
    stopReason: str  # "stop" | "length" | "toolUse" | "error" | "aborted"
    api: str
    provider: str
    model: str
    usage: Dict[str, Any]
    timestamp: float
    errorMessage: NotRequired[str]


class ToolCall(TypedDict):
    """Tool call content block."""

    type: str  # "toolCall"
    id: str
    name: str
    arguments: Dict[str, Any]


class ToolResultMessage(TypedDict):
    """Tool result message."""

    role: str  # "toolResult"
    toolCallId: str
    toolName: str
    content: List[ContentItem]
    details: Any  # Tool-specific details dict
    isError: bool
    timestamp: float


# ============================================================================
# Custom Message Support (Python equivalent of declaration merging)
# ============================================================================


class CustomAgentMessages(TypedDict, total=False):
    """Base interface for custom message types.

    Applications should subclass this to add custom message types:

    Example:
        class MyCustomMessages(CustomAgentMessages, total=False):
            notification: NotificationMessage
            artifact: ArtifactMessage

    Then use with AgentMessage:
        AgentMessage = Union[Message, MyCustomMessages]
    """

    pass


# Default AgentMessage - can be extended by applications
Message = Union[UserMessage, AssistantMessage, ToolResultMessage]
AgentMessage: TypeAlias = Message

# ============================================================================
# Agent State and Context
# ============================================================================


class AgentState(TypedDict, total=False):
    """Public agent state.

    Note: In Python, we use properties for readonly fields instead of getters.
    """

    systemPrompt: str
    model: Any  # Model reference
    thinkingLevel: str  # "off" | "minimal" | "low" | "medium" | "high" | "xhigh"
    tools: List[Any]  # List[AgentTool]
    messages: List[AgentMessage]
    isStreaming: bool
    streamingMessage: NotRequired[AssistantMessage]
    pendingToolCalls: List[str]  # List of toolCallIds
    errorMessage: NotRequired[str]


class AgentContext(TypedDict):
    """Context snapshot passed into the low-level agent loop."""

    systemPrompt: str
    messages: List[AgentMessage]
    tools: NotRequired[List[Any]]  # List[AgentTool]


# ============================================================================
# Tool Types
# ============================================================================


class AgentToolResult(TypedDict):
    """Result returned from tool execution."""

    content: List[ContentItem]
    details: Any


# Type alias for tool update callbacks
AgentToolUpdateCallback = Callable[[AgentToolResult], None]


class AgentTool(TypedDict):
    """Tool definition used by the agent runtime.

    Subclass examples should add parameter schemas with type hints.
    """

    name: str
    label: str  # For UI display
    description: str
    parameters: Dict[str, Any]  # JSON schema or type annotations
    execute: Callable[
        [str, Dict[str, Any], Any, AgentToolUpdateCallback], Awaitable[AgentToolResult]
    ]


# ============================================================================
# Tool Execution Configuration
# ============================================================================

ToolExecutionMode = str  # "sequential" | "parallel"


class BeforeToolCallResult(TypedDict, total=False):
    """Result from beforeToolCall hook."""

    block: bool
    reason: str


class BeforeToolCallContext(TypedDict):
    """Context passed to beforeToolCall."""

    assistantMessage: AssistantMessage
    toolCall: ToolCall
    args: Any  # Validated arguments
    context: AgentContext


class AfterToolCallResult(TypedDict, total=False):
    """Result from afterToolCall hook for mutating tool results."""

    content: NotRequired[List[ContentItem]]  # Replaces entire content
    details: NotRequired[Any]  # Replaces entire details
    isError: NotRequired[bool]  # Replaces error flag


class AfterToolCallContext(TypedDict):
    """Context passed to afterToolCall."""

    assistantMessage: AssistantMessage
    toolCall: ToolCall
    args: Any
    result: AgentToolResult
    isError: bool
    context: AgentContext


# ============================================================================
# Streaming and Events
# ============================================================================


class AssistantMessageEvent(TypedDict, total=False):
    """Event from streaming LLM response."""

    type: str
    partial: AssistantMessage
    contentIndex: int
    delta: str
    content: str
    contentSignature: str
    id: str  # For tool calls
    name: str
    toolName: str
    arguments: Dict[str, Any]


# Event types for the agent loop
AgentEventType = str


class AgentEvent(TypedDict, total=False):
    """Events emitted by the Agent for UI updates.

    agent_end is the last event emitted for a run, but awaited subscribers
    for that event are still part of run settlement.
    """

    type: str  # Event type discriminator

    # agent_start / agent_end
    messages: NotRequired[List[AgentMessage]]

    # turn_end
    message: NotRequired[AssistantMessage]
    toolResults: NotRequired[List[ToolResultMessage]]

    # message_start / message_end
    # message: AgentMessage - shared field

    # message_update (assistant only)
    assistantMessageEvent: NotRequired[AssistantMessageEvent]

    # tool_execution_start
    toolCallId: NotRequired[str]
    toolName: NotRequired[str]
    args: NotRequired[Any]

    # tool_execution_update
    partialResult: NotRequired[Any]

    # tool_execution_end
    result: NotRequired[AgentToolResult]
    isError: NotRequired[bool]


# ============================================================================
# Agent Loop Configuration
# ============================================================================


class AgentLoopConfig(TypedDict, total=False):
    """Configuration for the agent loop that doesn't change per run.

    Includes LLM streaming options (temperature, maxTokens, etc.)
    """

    # Required fields
    model: Any  # Model reference
    convertToLlm: Callable[
        [List[AgentMessage]], Union[List[Message], Awaitable[List[Message]]]
    ]

    # Optional fields
    transformContext: NotRequired[
        Callable[[List[AgentMessage], Any], Awaitable[List[AgentMessage]]]
    ]
    getApiKey: NotRequired[
        Callable[[str], Union[str, None, Awaitable[Union[str, None]]]]
    ]
    getSteeringMessages: NotRequired[
        Callable[[], Union[List[AgentMessage], Awaitable[List[AgentMessage]]]]
    ]
    getFollowUpMessages: NotRequired[
        Callable[[], Union[List[AgentMessage], Awaitable[List[AgentMessage]]]]
    ]
    toolExecution: NotRequired[ToolExecutionMode]
    beforeToolCall: NotRequired[
        Callable[
            [BeforeToolCallContext, Any], Awaitable[Union[BeforeToolCallResult, None]]
        ]
    ]
    afterToolCall: NotRequired[
        Callable[
            [AfterToolCallContext, Any], Awaitable[Union[AfterToolCallResult, None]]
        ]
    ]
    sessionId: NotRequired[str]
    thinkingBudgets: NotRequired[Dict[str, int]]
    transport: NotRequired[str]  # "sse" | "websocket" | "auto"

    # SimpleStreamOptions fields
    temperature: NotRequired[float]
    maxTokens: NotRequired[int]
    reasoning: NotRequired[str]  # Thinking level
    onPayload: NotRequired[Callable[[Any], None]]
    maxRetryDelayMs: NotRequired[int]
    apiKey: NotRequired[str]


# ============================================================================
# Stream Function Types
# ============================================================================


@runtime_checkable
class StreamFn(Protocol):
    """Protocol for stream function used by the agent loop.

    Contract:
    - Must not throw or return rejected promise for failures
    - Must return an AssistantMessageEventStream
    - Failures must be encoded in stream via events and final message with stopReason
    """

    def __call__(
        self, model: Any, context: AgentContext, options: AgentLoopConfig
    ) -> Any:  # Returns some stream type
        ...


# ============================================================================
# Helper Type Aliases
# ============================================================================

ThinkingLevel = str  # "off" | "minimal" | "low" | "medium" | "high" | "xhigh"
QueueMode = str  # "all" | "one-at-a-time"
MessageRole = str  # "user" | "assistant" | "toolResult" | custom roles
