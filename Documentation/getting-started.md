# Getting started with Chipix Studio

Chipix Studio is a workspace for **RTL design and hardware verification**. You describe what you want in plain language; Chipix designs or analyzes your hardware, proposes changes as reviewable diffs, and runs verification — with every step visible in one **conversation thread**.

## What you need

- Access to Chipix Studio with sign-in credentials from your team.
- Optional: local EDA tools (simulators, formal tools) if your organization runs verification on your machine. If tools are missing, the app tells you before a run starts.

## Sign in and pick a project

1. Open Chipix Studio and sign in with the credentials your team gave you.
2. Use the **project name** in the title bar (top) to switch projects or create a new one. You can also click the **Cx** mark on the left side rail and search projects from the command palette (**⌘K** / **Ctrl+K**).
3. Each project keeps its own files, verification runs, and conversations.

## Learn the layout

Chipix is organized around one main conversation with shortcuts on the left:

| Area | What it does |
| --- | --- |
| **Conversation thread** | Home screen — messages, files, runs, and results appear as cards here. |
| **Left side rail** | Open the editor, mental model, staged verification, task board, dashboard, theme, and history. |
| **Title bar** | Switch projects, jump to anything (**⌘K**), and return from overlays. |
| **Composer** | Type requests at the bottom; toggle **Design** vs **Verify** when both modes apply. |
| **Status bar** | Connection, project name, current phase, last verdict, and sound toggle. |

See [Workspace layout](workspace-layout.md) for a full map. New users can run **Take the workspace tour** from the command palette or the **History** panel.

## Your first design (greenfield)

When a project has no RTL yet, the thread shows **starter chips** — quick prompts such as UART, FIFO, or ALU. You can:

- Click a starter to seed the conversation, or
- Type your own request in the composer at the bottom.

Chipix may ask **clarifying questions** (interface, depth, clocking, and so on). Answer in the thread or in the focused clarifier overlay, then let Chipix generate files. New RTL appears as **file cards** in the thread — click a card to open the editor.

## Your first verification

Once RTL exists in the project:

1. Use the **Verify what you have** starter, or type something like “Run full verification on this design.”
2. You can also open the command palette (**⌘K** / **Ctrl+K**) and choose **Start verification on active design**.
3. Or click **Staged flow** on the left side rail for a plan-first path.
4. Progress appears as a **run card** in the thread; when finished, a **verdict card** summarizes pass or fail.

For a guided plan before execution, use **Plan then verify (staged)** from the palette — see [Running verification](running-verification.md).

## Where to go next

| Topic | Guide |
| --- | --- |
| Side rail, overlays, and navigation | [Workspace layout](workspace-layout.md) |
| Thread, cards, and starters | [The conversation thread](the-thread.md) |
| Design flow and clarifiers | [Designing RTL](designing-rtl.md) |
| Runs, staged verify, toolchain | [Running verification](running-verification.md) |
| Verdicts, failures, dashboard | [Understanding results](understanding-results.md) |
| Approve or reject AI edits | [Patches and diffs](patches-and-diffs.md) |
| Editors and IDE mode | [Files and editor](files-and-editor.md) |
| Task board and agent tasks | [Task board](task-board.md) |
| Shortcuts | [Keyboard shortcuts](keyboard-shortcuts.md) |
| Terms | [Glossary](glossary.md) |
| Problems | [Troubleshooting](troubleshooting.md) |

## Getting help in the app

- **Documentation** — left side rail **Settings** (opens the History panel) → **Documentation**, or command palette → **Open documentation**.
- **Feedback** — side rail **Letter from the founders** for product feedback, roadmap, and changelog.
- **The thread** — Ask Chipix directly; documentation is a stable reference when you want step-by-step guidance.
