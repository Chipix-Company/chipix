# Running verification

Verification checks your RTL against tests, assertions, or formal properties — depending on what your Chipix deployment is configured to run.

## Quick verification

Fastest path:

1. Ensure the correct project is selected.
2. Click **Verify what you have** (starter) or ask in the composer: “Run full verification on the active design.”
3. From the command palette (**⌘K**): **Start verification on active design**.
4. Open the **Dashboard** from the side rail and use **Start verification** when you want a structured entry point.

A **Run** card tracks progress. When the run finishes, a **Verdict** card appears. Open the dashboard for a structured summary (see [Understanding results](understanding-results.md)).

## Staged verification (plan first)

For more control you have two equivalent entry points:

- Command palette → **Plan then verify (staged)**
- Left side rail → **Staged flow**

Typical steps:

1. Chipix prepares context and may show a **recommended strategy** (unit simulation, formal, UVM, or combined — labels depend on your deployment).
2. Review or refine the **plan** in the thread.
3. Approve execution; the run card updates until completion.

Staged progress is saved per project so a refresh can restore plan cards in the thread.

## Re-running after changes

After you **apply a diff** or edit files manually:

- Use **Re-run verification** / **Re-check** starters, or
- Ask explicitly to re-verify after the change.

The thread may ask **Re-verify?** after an applied patch — confirm to start a new run.

## Toolchain checks

Before a long run, Chipix may verify that required simulators or formal tools are available. If something is missing, you will see an error in the thread or a blocking message — fix the environment or contact your administrator rather than retrying blindly.

## Cancelling runs

While a run is active, open the **Run** card and use **Cancel** when that action is available. Completed runs remain in history; you may be able to **Download** packaged outputs from the run card when your organization enables downloads.

## What gets verified

Verification uses the **active design** in the project — typically the latest RTL Chipix generated or you saved. If multiple versions exist, prefer opening files from the latest **File** cards or the IDE workspace to confirm you are on the intended revision.

## Task board

Large verification efforts may appear on the [Task board](task-board.md) as tasks move through **In verification** and **Completed**. Double-click a task to open its linked conversation.
