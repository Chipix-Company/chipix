# Understanding results

After verification, Chipix surfaces results at three levels: **thread cards** (fast scan), **dashboard** (structured detail), and optional **downloads** (artifacts for offline review).

## Verdict card (thread)

The verdict is the headline:

- Overall pass or fail
- Scenario or test counts when available
- A short status line while runs are still queued or running

Treat the verdict as the answer to “Did it work?” — not the full debug story.

The **status bar** also shows a compact last-verdict pill; click it to open the dashboard.

## When something fails

Failures are intentionally split into small cards:

1. **Verdict (failed)** — what failed at a glance.
2. **Why** — plain English, often quoting the exact line or condition.
3. **Waveform** — a few signals at the failure time when trace data is available.
4. **Diff (fix)** — a proposed patch; apply only after you agree.

Use **Open file at line …** links on failure cards to jump into the editor at the suspect location. Suggestion chips in the composer may offer **Explain failure** or **Propose fix**.

## Dashboard overlay

Open from:

- Left side rail → **Dashboard**
- History panel → **Dashboard**
- Command palette → **Open dashboard**
- **Open the dashboard** starter (after you have runs)
- Last-verdict pill on the status bar

The dashboard is optimized for one question: *“Did it work, and if not, what is the smallest evidence that explains why?”*

You will typically see:

- A headline (verdict and key numbers)
- Failure rows with reason, code snippet, optional waveform, and actions such as applying a fix back into the thread
- Design artifacts (RTL, specs, generated outputs) with **Open in IDE**
- Run history charts and token usage when your deployment shows them
- A thin coverage strip when coverage data exists

Narrative explanations and fix proposals are pushed back into the **thread** as Why/Diff cards so you can discuss them with Chipix.

## Coverage and scenarios

When detailed metrics are available, the dashboard shows scenario lists and coverage bars. If only coarse run status is available, you may see a simplified view with run ID and status — open the run card’s technical log for more detail.

## Comparing runs

Use the History panel → **Recent runs** or the dashboard run list to reopen past runs. Selecting a run from the palette or drawer focuses the workspace on that run’s context where supported.

## Downloads

Completed runs may offer **Download** on the run card (logs, waves, reports). If download is not available, ask your administrator or Chipix in the thread for the artifacts you need.

## Task board

Failed or in-progress verification work may also appear on the [Task board](task-board.md) under **In verification**. Use it alongside the thread when several checks are running in parallel.
