# Development Guide

Everything you need to build, run, test, and package Chipix Studio locally.

## Prerequisites

Install these before running locally:

- Windows 10/11 with PowerShell 5+ or PowerShell 7 (Linux/WSL supported for builds)
- Python 3.11 recommended
- Node.js 20+ and npm
- Git
- Preferred RTL parser:
  - Slang SystemVerilog CLI (`slang`)
  - If it is not on `PATH`, set `CHIPVERIFY_SLANG_BIN` to the full `slang.exe` path.
  - Packaged desktop builds also look for `runtime/bin/slang.exe`; the embedded Windows package script copies Slang there when it can find it.
  - If Slang is missing or fails, TruthCore falls back to a regex parser and records `parser_engine=regex_fallback`.
  - Set `CHIPVERIFY_REQUIRE_SLANG=1` to disable fallback and require compiler-grounded parsing.
- Optional EDA tools for stronger verification:
  - Icarus Verilog (`iverilog`)
  - Verilator
  - Yosys / SymbiYosys
  - Graphviz
- Optional local LLM runtime:
  - `llama-server.exe`
  - A GGUF model file

## Clone And Install

```powershell
git clone https://github.com/Chipix-Company/chipix.git
cd chipix

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt

npm install
cd frontend
npm install
cd ..
```

## Configure LLM Provider

The default development launcher uses Gemini unless you override it.

### Gemini

```powershell
$env:CHIPVERIFY_LLM_PROVIDER = "gemini"
$env:MODEL_PROVIDER = "gemini"
$env:GOOGLE_API_KEY = "your-gemini-api-key"
# GEMINI_API_KEY also works.
```

### OpenAI Official API

```powershell
$env:CHIPVERIFY_LLM_PROVIDER = "openai"
$env:MODEL_PROVIDER = "openai"
$env:MODEL_NAME = "gpt-4o"
$env:CHIPVERIFY_LLM_MODEL_ALIAS = "gpt-4o"
$env:OPENAI_API_KEY = "your-openai-api-key"
$env:OPENAI_API_BASE = "https://api.openai.com/v1"
$env:CHIPVERIFY_LLM_API_KEY = $env:OPENAI_API_KEY
$env:CHIPVERIFY_LLM_BASE_URL = "https://api.openai.com/v1"
```

OpenAI-compatible custom endpoints can still use `CHIPVERIFY_LLM_PROVIDER=openai`
with a different `OPENAI_API_BASE` / `CHIPVERIFY_LLM_BASE_URL`.

### Azure AI Foundry / Azure OpenAI-Compatible Provider

Use this for Azure-hosted deployments without changing code.

```powershell
$env:CHIPVERIFY_LLM_PROVIDER = "azure_openai"
$env:MODEL_PROVIDER = "azure_openai"
$env:AZURE_OPENAI_ENDPOINT = "https://<your-resource>.services.ai.azure.com/openai/v1"
$env:AZURE_OPENAI_DEPLOYMENT = "<deployment-name>"
$env:AZURE_OPENAI_API_STYLE = "deployment"
$env:AZURE_OPENAI_API_VERSION = "2024-10-21"
$env:AZURE_OPENAI_AUTH_HEADER = "api-key"

# Option A: API-key auth
$env:AZURE_OPENAI_API_KEY = "your-azure-api-key"

# Option B: Azure identity auth
$env:AZURE_OPENAI_USE_AAD = "true"
$env:AZURE_OPENAI_TOKEN_SCOPE = "https://ai.azure.com/.default"
```

### NVIDIA NIM

```powershell
$env:CHIPVERIFY_LLM_PROVIDER = "nim"
$env:MODEL_PROVIDER = "nim"
$env:NIM_API_KEY = "your-nim-api-key"
```

### Local GGUF Runtime

```powershell
$env:CHIPVERIFY_LLM_PROVIDER = "local"
$env:MODEL_PROVIDER = "local"
$env:CHIPVERIFY_GGUF_PATH = "C:\path\to\model.gguf"
$env:CHIPVERIFY_LLAMACPP_BIN = "C:\path\to\llama-server.exe"
```

The repo does not include model artifacts. Keep GGUF files outside git or in ignored runtime artifact folders.

## Start The App

Recommended Windows development launch:

```powershell
.\launch_all.ps1
```

This starts:

- Backend API: `http://127.0.0.1:7348`
- Frontend dev server: `http://localhost:5173`
- Electron desktop shell
- Local model runtime on `7349`, only when `MODEL_PROVIDER=local`

TruthCore uses a demo-friendly readiness gate by default: unresolved design
questions are shown as warnings but do not block staged planning. The launcher
also passes this explicitly:

```powershell
$env:CHIPVERIFY_DEMO_RELAX_MENTAL_MODEL_GATES = "true"
```

Set it to `false` for strict behavior that blocks planning until those questions are answered:

```powershell
$env:CHIPVERIFY_DEMO_RELAX_MENTAL_MODEL_GATES = "false"
```

## Manual Development Commands

Backend only:

```powershell
cd backend
uvicorn main:app --host 127.0.0.1 --port 7348
```

Frontend only:

```powershell
cd frontend
npm run dev
```

Electron shell:

```powershell
npm run dev:electron
```

All three with npm:

```powershell
npm run dev
```

## Verification Workflow

The staged verification flow is:

1. Upload or select a spec artifact.
2. Upload or select RTL source files or an RTL project archive.
3. Build or refresh the mental model from the active spec and RTL.
4. Show the mental model and strategy recommendations.
5. Choose Unit Simulation, Formal Verification, UVM Environment, or All.
6. Generate a reviewable plan before execution.
7. Refine the plan when needed.
8. Approve and execute.
9. Store generated artifacts, validation results, compile-gate results, and evidence.

The mental model is intended to be the shared source of truth for downstream agents. Generated verification collateral should be grounded in the persisted mental-model revision and should write results back as evidence.

## Tests And Checks

Backend syntax check example:

```powershell
python -m py_compile backend\routes\staged_verification.py
```

Backend tests:

```powershell
python -m pytest backend\tests -q
```

Frontend tests:

```bash
cd frontend
npm test
```

Frontend lint/build:

```powershell
cd frontend
npm run lint
npm run build
```

## Packaging

Build frontend:

```powershell
npm run build:frontend
```

Build backend EXE with PyInstaller:

```powershell
npm run build:backend:win
```

Build Windows Electron installer with embedded backend:

```powershell
npm run package:desktop:installer:win
```

Build native Linux AppImage and Debian installers from Linux or WSL:

```bash
npm run package:desktop:installer:linux
```

Build unpacked Windows Electron directory with embedded backend:

```powershell
npm run package:desktop:embedded:win
```

Build Linux AppImage with embedded backend (unified frontend + Python backend + bundled `.env`):

```bash
npm run package:desktop:installer:linux:embedded
```

Build browser-only Linux demo bundle (backend serves UI at `/app/`):

```bash
npm run package:nocert:webdemo:linux
```

For RHEL-compatible builds, use AlmaLinux 8 or `Dockerfile.packaging`:

```bash
docker build -f Dockerfile.packaging -t chipverify-packaging .
```

Packaging outputs are written to ignored build directories such as `dist-electron/`, `backend/runtime/windows/build/`, `backend/runtime/linux/build/`, `.packaging/`, and `build-resources/runtime/`.

## Desktop Release (Auto-Update)

After changing the app, ship a new desktop build and register the version in Convex so installed clients can auto-update.

### 1. Bump `package.json` version and build installer + `latest.yml`

Update the `version` field in root `package.json` before running the build. Artifacts are written to `dist-electron/` (installer `.exe` and `latest.yml`).

### 2. Upload release assets to GitHub Releases

Push a version tag to trigger the release workflow:

```bash
git tag v0.2.0
git push origin main --tags
```

### 3. Update Convex manually (optional)

```bash
./scripts/convex/set-release-version.sh --version 0.2.0 --platform linux --github-tag v0.2.0
```

Or PowerShell:

```powershell
.\scripts\convex\set-release-version.ps1 -Version "0.2.0" -ReleaseNotes "Your notes here"
```

This writes the canonical version to the Convex `appInfo` table. On next launch, packaged desktop apps compare their local version against Convex and auto-update when the cloud version is newer.

## Files Not To Commit

These are local runtime artifacts and should stay out of git:

- `.env` / `backend/.env`
- `.sys_id` / `backend/.sys_id`
- `chipverify.db` / `backend/chipverify*.db*`
- `logs/`, `backend/logs/`
- `outputs/`
- `.chipverify_tmp/`, `.packaging/`
- `dist/`, `dist-electron/`
- `node_modules/`
- generated project uploads and run output
- real license keys or activation files
- large GGUF model files

## Common Troubleshooting

Backend offline:

```powershell
Invoke-RestMethod http://127.0.0.1:7348/api/v1/health
```

Frontend offline:

```powershell
Invoke-WebRequest http://localhost:5173
```

Electron bridge missing:

- Restart using `.\launch_all.ps1`.
- Make sure no stale Electron process is running.
- Check the Electron terminal window.

LLM errors:

- Confirm `MODEL_PROVIDER` and `CHIPVERIFY_LLM_PROVIDER`.
- Confirm the correct API key env var is set.
- For local mode, confirm `CHIPVERIFY_GGUF_PATH` and `CHIPVERIFY_LLAMACPP_BIN`.
