# Chip-Verify-AI Agent Architecture Update

## Overview

This document describes the architectural transformation of the Chip-Verify-AI agent system to match the patterns from the `@mariozechner/pi-agent-core` TypeScript reference implementation.

## Architecture Transformation Summary

### What Changed

**Before:** Multiple competing agent architectures
- **Legacy.** Regex-based tool parsing in `agent_ws.py`
- **Legacy.** Sequential 6-phase pipeline in `orchestrator.py`
- **Modern.** Dynamic agentic loop in `agentic_loop.py`
- **Modern.** LangGraph patterns in `RTL_designer/`

**After:** Unified event-driven architecture
- ✅ **Single Agent runtime** - `backend/agent_core/`
- ✅ **Rich event system** - Streaming events for UI updates
- ✅ **Tool execution framework** - Parallel+Sequential modes
- ✅ **Message queuing** - Steering and Follow-up queues
- ✅ **Transform pipeline** - Context transformation + LLM conversion

### Where to Find the New Code

```
backend/agent_core/
├── __init__.py                    # Package exports
├── types.py                       # Type definitions
├── agent_loop.py                  # Core event loop
├── agent.py                       # High-level Agent class
├── event_stream.py                # Streaming abstractions
└── tests/                         # Test suite
```

## Core Concepts (TypeScript Patterns Applied)

### AgentMessage vs LLM Message

Following the TypeScript reference, we now distinguish between:

- **AgentMessage**: Flexible union of LLM messages + custom types
- **LLM Message**: Only `user` | `assistant` | `toolResult`

```python
# Python equivalent of TypeScript pattern
from agent_core import AgentMessage, Message

# Custom message types via Python protocols
def my_convert_to_llm(messages: List[AgentMessage]) -> List[Message]:
    return [
        m for m in messages
        if isinstance(m, (UserMessage, AssistantMessage, ToolResultMessage))
    ]
```

### Message Flow

```
AgentMessage[] → transformContext() → AgentMessage[] → convertToLlm() → Message[] → LLM
```

1. **transformContext**: Prune old messages, inject external context
2. **convertToLlm**: Filter out UI-only messages, convert custom types

### Event Flow

The agent now emits a rich event hierarchy for reactively updating UIs:

```
prompt("Hello")
├─ agent_start
├─ turn_start
├─ message_start { userMessage }
├─ message_end { userMessage }
├─ message_start { assistantMessage }
├─ message_update { assistantMessageEvent, partial }
├─ message_end { assistantMessage }
├─ turn_end { message, toolResults }
└─ agent_end { messages }
```

**Event Types**:
- `agent_start` / `agent_end` - Run lifecycle
- `turn_start` / `turn_end` - Turn lifecycle (one LLM call + tools)
- `message_start` / `message_update` / `message_end` - Messages
- `tool_execution_start` / `update` / `end` - Tool execution

## Using the New System

### Basic Usage

```python
from agent_core import Agent, AgentTool, AgentOptions

# Initialize agent
options = AgentOptions(
    initialState={
        'systemPrompt': 'You are a helpful assistant',
        'model': my_model,
        'tools': [my_tools]
    }
)

agent = Agent(options)

# Subscribe to events
unsubscribe = agent.subscribe(lambda event, signal:
    print(f"Event: {event['type']}", signal)
)

# Send a prompt
await agent.prompt('Hello!')

# Continue the conversation
await agent.continueRun()
```

### Steering and Follow-up Messages

Unique to this architecture: inject messages mid-run vs wait-for-completion.

```python
# Steering: Inject mid-run (after current tool batch)
agent.steer({
    'role': 'user',
    'content': [{
        'type': 'text',
        'text': 'Stop! Focus on verification instead.'
    }],
    'timestamp': time.time()
})

# Follow-up: Wait for agent to finish, then continue
agent.followUp({
    'role': 'user',
    'content': 'Also generate the testbench.',
    'timestamp': time.time()
})
```

### Custom Message Types

Extend AgentMessage with your own types:

```python
from typing import TypedDict
from agent_core import AgentMessage

class NotificationMessage(TypedDict):
    role: str  # "notification"
    text: str
    timestamp: float

# Use in convertToLlm to filter out
def convert_with_notifications(messages: List[AgentMessage]):
    return [m for m in messages if m.get('role') != 'notification']

options = AgentOptions(
    convertToLlm=convert_with_notifications
)
```

### Tool Execution with Hooks

Control tool execution before and after:

```python
async def before_bash(context, signal):
    if context['toolCall']['name'] == 'bash':
        return {
            'block': True,
            'reason': 'Bash execution disabled in sandbox'
        }

async def after_tool_log(context, signal):
    result = context['result']
    if not context['isError']:
        result['details'] = {**result.get('details', {}), 'audited': True}
    return result

config = AgentOptions(
    beforeToolCall=before_bash,
    afterToolCall=after_tool_log
)
```

### Parallel vs Sequential Tool Execution

```python
# Parallel: preflight sequential, execute concurrently
options = AgentOptions(toolExecution='parallel')  # Default

# Sequential: one-by-one execution
options = AgentOptions(toolExecution='sequential')
```

### State Management

The Agent provides reactive state updates:

```python
# Read current state
state = agent.state
print(f"Streaming: {state['isStreaming']}")
print(f"Pending tools: {len(state['pendingToolCalls'])}")

# Modify mutable state
agent.state.systemPrompt = "New prompt"
agent.state.tools = [new_tools]
agent.state.messages = []  # Clear history
```

### Transform Context

Transform messages before sending to LLM:

```python
MAX_TOKENS = 8000

async def prune_old_context(messages: List[AgentMessage], signal) -> List[AgentMessage]:
    if estimate_tokens(messages) > MAX_TOKENS:
        return prune(messages)  # Custom pruning logic
    return messages

options = AgentOptions(
    transformContext=prune_old_context
)
```

## Migration Guide

### From `orchestrator.py` to Agent Loop

The old `ChipVerifyOrchestrator` has been replaced with a more flexible event-driven system that maintains the same verification pipeline but allows for dynamic execution.

**Old pattern:**
```python
# Deprecated
orchestrator = ChipVerifyOrchestrator()
await orchestrator.run_pipeline(spec_file, rtl_files)
```

**New pattern:**
```python
# New event-driven approach
builder = ChipVerifyBuilder(agent)
await builder.ingest_spec(spec_file)
await builder.ingest_rtl(rtl_files)

# Events emitted for each phase
builder.subscribe(lambda event, signal:
    print(f"Phase: {event['phase']}, Progress: {event['progress']}")
)

await builder.run()
```

### From `agent_ws.py` to Event Streaming

The WebSocket handler has been refactored to use the new event system:

**File: `backend/routes/agent_ws.py`**

|**Old Pattern**|**New Pattern**|
|---|---|
|Regex-based tool parsing|Native function calling via `convertToLlm`|
|Manual event emission|Automatic via agent loop|
|Simple states|Rich event hierarchy|

### From `agents/` to Custom Tools

Individual agents from `backend/original_core/agents/` should be converted to tools:

**Before:** Separate agent classes
```python
class RTLAnalyzer:
    def analyze(self, rtl_content):
        # ...
```

**After:** Tool definitions
```python
analyze_tool = AgentTool(
    name="analyze_rtl",
    label="Analyze RTL",
    description="Parse Verilog/SystemVerilog for structure",
    parameters={"rtl_path": str},
    execute=async def(tool_call_id, params):
        analysis = await analyze_rtl(params['rtl_path'])
        return {"content": [{"type": "text", "text": analysis}], "details": {}}
)
```

### From `parallel_agents.py` to Queue Management

The parallel orchestration has been generalized:

**Before:** Hardcoded parallelism
```python
tasks = [
    AgentTask(agent='spec_parser', input=spec_file),
    AgentTask(agent='rtl_analyzer', input=rtl_files)
]
orchestrator.run_parallel(tasks)
```

**After:** Queue-based steering
```python
# Spec and RTL run in parallel via queue modes
builder = ChipVerifyBuilder(agent)
builder.steeringMode = "one-at-a-time"  # Critical ordering
builder.parallelMode = True  # New flag
```

## Testing

Comprehensive test suite in `backend/agent_core/tests/`:

```bash
cd backend/agent_core
python -m pytest tests/
```

**Test Coverage:**
- ✅ Agent loop event sequences
- ✅ Tool execution (parallel/sequential)
- ✅ Steering and follow-up message injection
- ✅ State transitions
- ✅ Error handling and aborts
- ✅ Custom message type conversion
- ✅ Transform context pipeline
- ✅ before/after tool hooks

## Benefits

| Area | Benefit |
|------|---------|
| **Event-Driven** | Rich UI updates, streaming, and responsive UX |
| **Flexible Execution** | Dynamic tool ordering vs hardcoded phases |
| **Message Queuing** | Mid-run steering and delayed follow-ups |
| **Tool Hooks** | Security, auditing, and result mutation |
| **Transform Pipeline** | Context window management, external state injection |
| **Unified API** | Single Agent class for both basic and advanced use |

## Integration Examples

### With Frontend (React)

```javascript
// React component example
const AgentUI = () => {
  const [messages, setMessages] = useState([]);
  const [streaming, setStreaming] = useState(false);
  
  useEffect(() => {
    const unsub = agent.subscribe((event) => {
      switch(event.type) {
        case 'message_start':
          if (event.message.role === 'assistant') {
            setStreaming(true);
          }
          break;
        case 'message_update':
          setMessages(prev => [...prev, event.message]);
          break;
        case 'message_end':
          if (event.message.role === 'assistant') {
            setStreaming(false);
          }
          break;
      }
    });
    
    return unsub;
  }, []);
  
  return <Chat messages={messages} streaming={streaming} />;
};
```

### With FastAPI

```python
from fastapi import WebSocket

@app.websocket("/ws")
async def agent_websocket(websocket: WebSocket):
    await websocket.accept()
    
    agent = Agent(ws_options)
    
    unsub = agent.subscribe(lambda event, signal:
        websocket.send_json(event)
    )
    
    try:
        while True:
            data = await websocket.receive_json()
            if data['type'] == 'prompt':
                await agent.prompt(data['message'])
            elif data['type'] == 'continue':
                await agent.continueRun()
    finally:
        unsub()
```

## Future Enhancements

Following the reference implementation roadmap:

- [ ] **Proxy Support** - Backend-proxy for browser apps
- [ ] **Knowledge Graph Integration** - Embeddings and nearest neighbor search
- [ ] **Regression Intelligence** - Test failure prediction
- [ ] **Self-healing Tests** - Auto-adapt testbenches to RTL changes
- [ ] **Formal Verification Agent** - SVA property generation
- [ ] **Enterprise Features** - Multi-project, team management

## Resources

- **Reference Implementation**: `@mariozechner/pi-agent-core` (TypeScript)
- **Test Suite**: `backend/agent_core/tests/`
- **Examples**: `backend/agent_core/examples/`
- **Migration Guide**: See sections above

## Questions?

Open an issue on the repository or consult the agentic-loop.py source for the canonical event flow.