"""
Backend Agent Core Package

Event-driven agent runtime following @mariozechner/pi-agent-core TypeScript patterns.
"""

from .types import (
    # Message content types
    TextContent,
    ImageContent,
    ContentItem,
    # Message types
    UserMessage,
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    Message,
    AgentMessage,
    # Custom message support
    CustomAgentMessages,
    # State and context
    AgentState,
    AgentContext,
    # Tool types
    AgentToolResult,
    AgentToolUpdateCallback,
    AgentTool,
    # Tool execution
    ToolExecutionMode,
    BeforeToolCallResult,
    BeforeToolCallContext,
    AfterToolCallResult,
    AfterToolCallContext,
    # Streaming and events
    AssistantMessageEvent,
    AgentEvent,
    # Configuration
    AgentLoopConfig,
    StreamFn,
)

from .event_stream import (
    EventStream,
    EventStreamAdapter,
    collect_events,
    event_stream,
)

from .agent_loop import (
    agent_loop,
    agent_loop_continue,
    run_agent_loop,
    run_agent_loop_continue,
    _run_loop,
    _execute_tool_calls,
    _stream_assistant_response,
)

from .agent import (
    Agent,
    AgentOptions,
    AgentRun,
    PendingMessageQueue,
    MutableAgentState,
)

# Version
__version__ = "1.0.0"

# Package docstring
__doc__ = """
Agent Core - Event-driven agent runtime

This package implements the @mariozechner/pi-agent-core TypeScript patterns
in Python, providing:

- Event-driven agent runtime with rich lifecycle events
- Tool execution framework (parallel/sequential modes)
- Message queuing (steering and follow-up)
- State management and streaming
- Transform pipeline (context + LLM conversion)

Main components:
- Agent: High-level stateful agent wrapper
- agent_loop: Low-level event-driven runtime
- EventStream: Async streaming abstractions
- types: Comprehensive type definitions

Usage:
    from agent_core import Agent, AgentTool
    
    agent = Agent({
        'initialState': {
            'systemPrompt': "You are a helpful assistant",
            'model': my_model,
            'tools': [tools]
        }
    })
    
    agent.subscribe(lambda event, signal: print(event['type']))
    await agent.prompt("Hello")
"""
