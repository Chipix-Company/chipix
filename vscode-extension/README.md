# Chipix VS Code Extension

This folder contains the first VS Code client for Chipix. It is intentionally isolated from the existing desktop app.

## Run Locally

1. Start the existing Chipix backend on `http://127.0.0.1:7348`.
2. In this folder, run:

```powershell
npm install
npm run compile
```

3. Open this folder in VS Code and press `F5` to launch an Extension Development Host.
4. Open an RTL workspace in the development host and use the Chipix activity-bar view.

## What V1 Supports

- Connects to the existing backend APIs.
- Creates/loads a project for the current workspace.
- Uploads a spec file and RTL workspace.
- Builds and displays TruthCore in a webview.
- Prepares, plans, and executes staged verification through backend APIs.
- Syncs generated artifacts into `chipix_out/`.
- Uploads simulator logs and shows repair patches.
- Opens generated files with VS Code's native editor.
- Opens patch proposals with VS Code's native diff editor.

## Design Rule

Do not modify the existing desktop app for this extension MVP. Shared packages can be introduced later after the extension workflow is proven.
