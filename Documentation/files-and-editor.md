# Files and editor

Chipix treats the conversation as primary. Files appear when you need them — as **cards** in the thread or through explicit editor modes.

## File cards

When Chipix creates or updates RTL, a **File** card appears in the thread with the filename and type. **Click the card** to open the **single-file editor** (file peek):

- Full-screen focus on one file
- Syntax highlighting for SystemVerilog/Verilog and common sidecar types
- **Save** with **⌘S** / **Ctrl+S**
- **Back to conversation** returns to the thread
- Footer prompts for file-aware questions (“explain this module”, and similar)

Revision context (who wrote the file and when) appears in the editor when Chipix provides it.

## IDE workspace

For multi-file work, open **IDE workspace**:

- Left side rail → **Editor**
- History panel → **Open IDE workspace**
- Command palette → **Open IDE workspace** (**⌘⇧E** / **Ctrl+Shift+E**)

The IDE includes:

- **File explorer** — specifications, RTL sources, generated outputs, and other project files
- **Editor pane** — edit with save per file
- **Upload, rename, and delete** — manage project files from the explorer
- **Signals panel** — hierarchy and schematic for RTL files when visualization is available
- **IDE assistant** — a separate chat scoped to the file you are editing
- **Bottom panel** — output, telemetry, diffs, and test/run shortcuts

Save each file you change; paths inside multi-file RTL projects stay relative to the project layout.

## Live write peek

During streaming generation, the composer may offer a **peek** panel showing code as it arrives. This is read-only preview until Chipix finishes; then a proper **File** card is added.

## History panel and project files

Open **Threads** or **Settings** on the side rail. Sections include:

- **Conversations**
- **Workspace** (dashboard, IDE, documentation, tour)
- **Patches**
- **Recent runs**
- **Project files** — click a row to open the quick editor

## Attachments

Use **Attach** in the composer to upload specs or RTL into the active project, or to add up to a few workspace files to your next message. Upload options depend on what your deployment allows.

## Attach from workspace

When attaching from existing project files, search and pick files by category (spec, RTL, other). Selected files ride along with your message as context.

## What is stored where

- **Project** — artifacts, runs, and settings shared across conversations.
- **Conversation** — thread history and cards; switching threads does not delete project files.
