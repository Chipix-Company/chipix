# Workspace layout

Chipix Studio is built around one **conversation thread**. Everything else — editor, dashboard, mental model, documentation — opens as an **overlay** on top of the thread or beside it. You always return to the conversation with **Back**, **Escape**, or by closing the overlay.

## Title bar

- **Project switcher** — Shows the active project. Open it to search projects, create a new project, or start a new conversation.
- **Breadcrumb** — Shows where you are (Conversation, Dashboard, Editor, Documentation, and so on).
- **Back** — Returns to the conversation from an overlay.
- **Jump to anything** — Opens the command palette (**⌘K** / **Ctrl+K**).

## Left side rail

Icons run top to bottom. Click an icon to open that surface; click again to close it when the icon acts as a toggle.

| Icon | Opens |
| --- | --- |
| **Cx** | Command palette and project switcher |
| **Threads** | History panel — conversations, runs, files, patches |
| **Editor** | Full IDE workspace for multi-file editing |
| **Mental model** | Graph of how Chipix understands your design |
| **Staged flow** | Plan-first verification (prepare → plan → run) |
| **Task board** | Kanban view of project tasks |
| **Dashboard** | Metrics, runs, artifacts, and verification summary |
| **Letter from the founders** | Feedback, roadmap, and changelog |
| **Theme** | Switch light or dark mode |
| **Settings** | Same as **Threads** — opens the History panel |

## History panel

Opened from **Threads** or **Settings** on the side rail. Sections include:

- **Conversations** — Switch threads, start a new conversation, or remove inactive ones.
- **Workspace** — Dashboard, IDE, documentation, and workspace tour.
- **Patches** — Pending diffs waiting for your review.
- **Recent runs** — Open a past run in the dashboard.
- **Project files** — Open any project file in the quick editor.

## Conversation thread

The center of the workspace. Design, verification, questions, files, diffs, runs, and verdicts appear here as **cards** in order. See [The conversation thread](the-thread.md).

## Composer

At the bottom of the thread:

- Type your message and press **⌘Enter** / **Ctrl+Enter** to send.
- **Design / Verify** toggle — Switches what Chipix optimizes for. Verify mode may ask you to build a mental model first when Chipix needs to understand existing RTL.
- **Attach** — Upload specs, RTL files, or folders when your deployment allows uploads.
- **Suggestion chips** — Context-aware next steps (verify, explain failure, open dashboard, and similar).

## Status bar

Along the bottom edge:

- **Connection** — Whether Chipix Studio is connected to your server.
- **Project name** — Click to open the command palette.
- **Design / Verify lens** — Current mode and phase (designing, ready to verify, and so on).
- **Activity** — Live work in progress with a timer when Chipix is busy.
- **Last verdict** — Quick pass/fail from the most recent run; click to open the dashboard.
- **Sound** — Mute or unmute UI sounds.
- **Clock** — Local time.

## Overlays

Only one primary overlay is active at a time (plus optional quick file peek over the conversation):

| Overlay | Best for |
| --- | --- |
| **Dashboard** | Run history, pass rates, artifacts, starting verification |
| **Mental model** | Checking Chipix understood your RTL before big changes |
| **IDE workspace** | Editing many files, signals view, IDE assistant |
| **Documentation** | These user guides |
| **Task board** | Tracking design and verification tasks |
| **File peek** | Focused edit of a single file from a thread card |

Press **Escape** to close the topmost surface (palette, drawer, overlay, editor, clarifier).

## Workspace tour

If you are new, run **Take the workspace tour** from the command palette or History panel. It highlights the thread, side rail, project menu, composer, and Design/Verify toggle. You can replay the tour anytime.

## Command palette

**⌘K** / **Ctrl+K** is the fastest way to jump:

- Start a project or conversation
- Open IDE, dashboard, documentation, mental model
- Start or plan verification
- Search files and recent runs

See [Keyboard shortcuts](keyboard-shortcuts.md) for the full list.
