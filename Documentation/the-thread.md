# The conversation thread

The thread is the **home screen** of Chipix Studio. Design, verification, file delivery, and explanations all appear as **cards** in chronological order — not as separate tabs you have to hunt through.

## Message types you will see

| Card | What it means |
| --- | --- |
| **You** | Your message. Use **Edit** on a past message to change it and rewind the conversation from that point. |
| **Chipix** | Explanation from Chipix (markdown supported). |
| **Question** | Quick-reply choices (for example baud rate, FIFO depth). |
| **Plan** | What Chipix intends to do next — approve, refine, or adjust before work continues. |
| **File** | A generated or updated artifact — click to open in the editor. |
| **Diff** | A proposed code change — review before applying. |
| **Run** | Verification in progress or finished; expand for a technical log if you need it. |
| **Verdict** | Pass or fail summary with counts. |
| **Why** | Plain-language failure explanation, often with a line reference. |
| **Waveform** | A small signal view at the failure time (when available). |
| **Break** | A visual separator when you start a new iteration after a completed chapter. |

Some sessions also show **mental model**, **staged prepare**, **strategy**, **coverage**, or **task list** cards when those steps are part of your flow.

## Adaptive starters

At the bottom of an empty or idle thread, **starter chips** change with project state and your **Design / Verify** lens:

- **No files yet** — design templates (UART, FIFO, ALU) or import existing RTL.
- **Files, no runs yet** — verify, mental model, extend design, or start over.
- **After at least one run** — continue iterating, re-verify, open dashboard, or new design.

Starters with a built-in action (verify, mental model, dashboard) run immediately without an extra chat turn.

## Multiple conversations per project

Open the **History** panel from the side rail (**Threads** or **Settings**) → **Conversations** to start or switch threads. Each conversation has its own history; files and runs belong to the **project**.

Use the command palette to **Switch conversation** when you have more than one thread.

## Live generation

While Chipix writes RTL, a **live write peek** can open from the composer showing code as it streams in. Close it anytime; the finished file still appears as a card in the thread.

## Mental model

**Show mental model** (side rail, palette, or starter) opens a graph of how Chipix understands your design — hierarchy, interfaces, and intent — before you change or verify anything. In the thread you may see a compact mental model card with **Looks right** or **Something's off** before verification continues.

Use it when you want confidence that Chipix read your RTL correctly.

## Section breaks

After a completed run or verdict, your next message may add an **Iteration** break label so long sessions stay scannable.

## Design and verify gates

Chipix sometimes pauses with a clear choice:

- After design — open the editor or build a mental model first.
- Before verification — plan and run, review RTL in the editor, or upload missing files.
- After import — build a mental model or review files before tests start.

Respond in the thread; you stay in control of when simulation or formal runs start. See [Designing RTL](designing-rtl.md) and [Running verification](running-verification.md).
