# Troubleshooting

Practical fixes for common issues in Chipix Studio. For server or license problems, contact your administrator.

## I cannot sign in

- Confirm the server URL in **Settings** matches what your team provided.
- Self-registration may be disabled; ask an admin for credentials.
- Check network or VPN access to your Chipix server.

## No project selected / “Pick a project”

Use the **project switcher** in the title bar (or **Cx** on the side rail) to select or create a project before sending messages.

## Documentation is empty or fails to load

- Click **Refresh** or **Try again** in the documentation overlay.
- Reconnect if you were offline, then open documentation again.
- Ask your administrator if guides are missing from your deployment.
- You can still ask Chipix in the thread.

## Verification will not start

- Ensure RTL exists in the project (at least one **File** card or a file in the History panel under **Project files**).
- Read toolchain messages — missing simulators or formal tools must be installed where verification runs (your machine or server — ask your administrator).
- Try **Plan then verify (staged)** or **Staged flow** on the side rail to see preparation errors before execution.
- Switch to **Verify** in the composer; Chipix may require a **mental model** before verifying imported RTL.

## Run stuck on “running” or “queued”

- Large formal jobs can take a long time — wait for capacity to free up.
- Refresh the app; run cards often reconcile when the connection restores.
- Ask an administrator to inspect the run if it exceeds expected duration.

## I do not see waveforms or detailed failures

- Not every run exports trace data; simplified verdicts still appear in the thread.
- Open the run card’s technical log if available.
- Use the dashboard when metrics exist; otherwise ask Chipix to explain the log excerpt.

## Applied a diff but tests still fail

- Confirm you **re-verified** after apply.
- Open the file in the editor and check that ports and parameters match your intent.
- Use **Edit** on an earlier user message to rewind and try a different approach (see [The conversation thread](the-thread.md)).

## Command palette (**⌘K**) does not open

Close the clarifier, editor, or other overlays with **Escape**, then try again.

## I am lost in overlays

Press **Escape** repeatedly or use **Back** in the title bar to return to the conversation. See [Workspace layout](workspace-layout.md) for how overlays stack.

## Changes I made in the editor disappeared

- Ensure you saved (**⌘S**).
- Multi-file projects require saving each file in IDE mode.
- If you rewound the thread, later messages and file states may have been rolled back intentionally.

## Chipix stopped mid-response

- Check your connection to the server (status bar indicator).
- Send a short follow-up (“continue”) or restart the last action from starters.
- Start a **new conversation** from the History panel if the thread state looks corrupted.

## Task board will not load or tasks look stale

- Click **Retry** if the board shows an error.
- Refresh the page and reopen **Task board** from the side rail.
- Double-click a task to open its conversation for the full story.

## Still stuck?

1. Note the **project name**, **run ID** (from the run card or dashboard), and time of the issue.
2. Capture the error text from the thread or documentation overlay.
3. Use **Letter from the founders** → **Share feedback** if your build includes the feedback portal.
4. Contact your Chipix administrator with those details.
