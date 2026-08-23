# Task board

The task board is a **kanban view** of work in your project — design tasks, verification runs, and agent-driven jobs — organized by status.

## Opening the task board

- Left side rail → **Task board**
- Command palette → search for **Task board** if listed

The board opens as an overlay. Press **Escape** or use **Back** in the title bar to return to the conversation.

## Columns

Tasks move through four columns:

| Column | Meaning |
| --- | --- |
| **To do** | Queued or not started |
| **In progress** | Chipix or an agent is actively working |
| **In verification** | Verification is running or awaiting results |
| **Completed** | Finished successfully or closed |

Drag a task card between columns when your deployment allows manual status updates.

## Task cards

Each card shows:

- Title and short description
- Priority (low, medium, high)
- Progress when work is underway
- Agent or owner label when assigned

## Search and filters

Use **Search** to find tasks by title or description. Filter by **priority** or **agent** to narrow a busy board.

## Add a task

Click **Add task** to create work with:

- Title and instructions
- Optional agent name
- Priority

**Create & start agent** queues the task and begins work when your deployment supports agent tasks.

## Link to conversations

**Double-click** a task to open the conversation thread linked to that task. Use this when you need the full card history behind a board item.

## When to use the board vs the thread

- **Thread** — step-by-step design, diffs, runs, and explanations in order.
- **Task board** — overview of parallel work, status at a glance, and jumping between conversations tied to tasks.

See [Running verification](running-verification.md) and [Understanding results](understanding-results.md) for how verification appears in the thread and dashboard.
