# ChipVerify Runtime Launcher (Linux-first, Windows-supported)

This folder contains cross-platform runtime launchers for enterprise on-prem deployments:

- `start_runtime_linux.sh` (primary production target)
- `start_runtime_windows.ps1` (test-device support)
- `health_check.py` (cross-platform readiness probe)
- `diagnostics_bundle.py` (redacted support bundle generator)
- `validate_tls_endpoint.py` (HTTPS endpoint validator for reverse-proxy deployments)
- `tls/README.md` + `tls/nginx-chipverify.conf.example` (TLS fronting templates)
- `secret-rotation-runbook.md` (secret storage and rotation procedure)
- `operations-runbook.md` (install/upgrade/rollback/incident response procedures)
- `systemd/chipverify-runtime.service` (Linux service template)
- `windows/install_chipverify_service.ps1` (Windows NSSM service install template)
- `windows/build_backend_exe.ps1` (build backend as a Windows EXE bundle via PyInstaller)
- `linux/build_backend_elf.sh` (build backend as a Linux ELF bundle via PyInstaller)
- `linux/backend_service_entrypoint.py` (Linux PyInstaller entrypoint)
- `windows/runtime.env.example` (minimal production env template)
- `windows/cleanup_runtime_workspace.ps1` (safe cleanup for generated runtime artifacts)

## What these scripts do

1. Start `llama-server` with your GGUF model.
2. Wait for model runtime health (`/health`) to become ready.
3. Export ChipVerify local-provider environment values.
4. Start backend API (`uvicorn main:app`).

## Required environment variables

- `CHIPVERIFY_GGUF_PATH`: absolute path to your GGUF model file.
- `CHIPVERIFY_SECRET_KEY`: must be set to a non-default value (backend startup will fail otherwise).
- `CHIPVERIFY_LLM_API_KEY`: required by default because `CHIPVERIFY_LLM_API_KEY_REQUIRED=true`.

## Optional environment variables

- `CHIPVERIFY_LLAMACPP_BIN` (default: `llama-server` / `llama-server.exe`)
- `CHIPVERIFY_PYTHON_BIN` (default: `python3` / `python`)
- `CHIPVERIFY_ARTIFACT_MANIFEST_PATH` (optional; when set, launchers verify llama-server + GGUF sha256 before startup)
- `CHIPVERIFY_LLM_BIND_HOST` (default: `127.0.0.1`)
- `CHIPVERIFY_LLM_PORT` (default: `7349`)
- `CHIPVERIFY_LLM_CONTEXT_SIZE` (default: `8192`)
- `CHIPVERIFY_LLM_PARALLEL` (default: `2`)
- `CHIPVERIFY_LLM_API_KEY` (optional; must match llama-server `--api-key`)
- `CHIPVERIFY_BACKEND_HOST` (default: `127.0.0.1`)
- `CHIPVERIFY_BACKEND_PORT` (default: `7348`)
- `CHIPVERIFY_LOG_DIR` (default: `backend/logs`)
- `CHIPVERIFY_LLM_MODEL_ALIAS` (default: GGUF filename)
- `CHIPVERIFY_LLM_API_KEY_REQUIRED` (default: `true`)
- `CHIPVERIFY_ALLOW_PUBLIC_BIND` (default: `false`; blocks `0.0.0.0`/`::` binds unless explicitly enabled)

Health-check script inputs:
- `CHIPVERIFY_BACKEND_URL` (default: `http://127.0.0.1:7348/api/v1`)
- `CHIPVERIFY_LLM_BASE_URL` (default: `http://127.0.0.1:7349/v1`)

Security defaults in launchers:
- backend and model runtime default to loopback bind (`127.0.0.1`)
- public bind hosts are rejected unless `CHIPVERIFY_ALLOW_PUBLIC_BIND=true`
- API key is required unless `CHIPVERIFY_LLM_API_KEY_REQUIRED=false`

For multi-host access, keep runtime services on loopback and expose HTTPS via a reverse proxy.
See `backend/runtime/tls/README.md` for the deployment template.

## Artifact verification (recommended for enterprise)

If you provide `CHIPVERIFY_ARTIFACT_MANIFEST_PATH`, the launchers will verify sha256 checksums for:

- `llama-server` / `llama-server.exe`
- the configured GGUF file

Use [backend/runtime/artifacts/artifacts.manifest.example.json](backend/runtime/artifacts/artifacts.manifest.example.json) as the template for your manifest.

You can also run verification directly:

```bash
python backend/runtime/verify_artifacts.py --manifest /path/to/artifacts.manifest.json --require-expected
```

## Linux usage

```bash
export CHIPVERIFY_GGUF_PATH=/opt/chipverify/models/qwen-3.5-2b.gguf
export CHIPVERIFY_SECRET_KEY=<set-non-default-secret>
export CHIPVERIFY_LLM_API_KEY=<set-runtime-api-key>
bash backend/runtime/start_runtime_linux.sh
```

## Windows usage

```powershell
$env:CHIPVERIFY_GGUF_PATH = "C:\chipverify\models\qwen-3.5-2b.gguf"
$env:CHIPVERIFY_SECRET_KEY = "<set-non-default-secret>"
$env:CHIPVERIFY_LLM_API_KEY = "<set-runtime-api-key>"
.\backend\runtime\start_runtime_windows.ps1
```

Frontend endpoint contract for production:

- set `VITE_API_URL` to backend base URL only (example: `https://chipverify.example.com`)
- do not append `/api/v1`; frontend API calls already include the `/api/v1/...` path

See `frontend/.env.production.example` for a template.

## Windows backend EXE build (optional)

Build backend as an EXE bundle:

```powershell
.\backend\runtime\windows\build_backend_exe.ps1 -BackendRoot "C:\chipverify\backend"
```

The generated EXE launches Uvicorn using:

- `CHIPVERIFY_BACKEND_HOST` (default: `127.0.0.1`)
- `CHIPVERIFY_BACKEND_PORT` (default: `7348`)

## Linux backend ELF build (embedded desktop / user testing)

Build backend as a PyInstaller `--onedir` ELF bundle:

```bash
bash backend/runtime/linux/build_backend_elf.sh
```

Build the full embedded Linux desktop AppImage (frontend + Electron + backend + bundled `.env`):

```bash
npm run package:desktop:installer:linux:embedded
```

Browser-only no-cert demo bundle (backend serves UI at `/app/`):

```bash
npm run package:nocert:webdemo:linux
```

For RHEL-compatible artifacts, build on AlmaLinux 8 or use `Dockerfile.packaging`:

```bash
docker build -f Dockerfile.packaging -t chipverify-packaging .
docker run --rm -v "$(pwd)/dist-electron:/src/dist-electron" chipverify-packaging
```

Install service in EXE mode:

```powershell
.\backend\runtime\windows\install_chipverify_service.ps1 -BackendRoot "C:\chipverify\backend" -Mode backend-exe -RuntimeEnvFile "C:\chipverify\backend\runtime\windows\runtime.env"
```

Default service mode remains `runtime-script` (starts llama-server + backend together).

In `backend-exe` mode, the model runtime must be managed separately and match the backend local-runtime contract:

- `CHIPVERIFY_LLM_PROVIDER=local`
- `CHIPVERIFY_LLM_BASE_URL=http://127.0.0.1:7349/v1`
- `CHIPVERIFY_LLM_API_KEY` consistent with the model runtime

Desktop policy endpoint should remain aligned to backend service bind values:

- `CHIPVERIFY_BACKEND_URL=http://127.0.0.1:7348`

## Safe cleanup (generated artifacts only)

To remove non-essential generated files (logs, Python caches, temporary llama.cpp source clone):

```powershell
.\backend\runtime\windows\cleanup_runtime_workspace.ps1
```

## Health probe

```bash
python backend/runtime/health_check.py
```

`health_check.py` exits with code `0` only when both backend and local model runtime are healthy.

## Model output smoke test

Run a lightweight quality smoke test against the OpenAI-compatible `/v1/chat/completions` endpoint:

```bash
python backend/runtime/test_model_output.py
```

Custom endpoint / key example:

```bash
python backend/runtime/test_model_output.py --base-url http://127.0.0.1:7349/v1 --api-key "$CHIPVERIFY_LLM_API_KEY"
```

The script validates:

- runtime `/health` is `ok`
- `/v1/models` returns a model id
- chat completions are non-empty and contain verification-oriented keyword signals

Expected response contract:

- Backend health: `GET ${CHIPVERIFY_BACKEND_URL}/health` returns HTTP `200` JSON with at least `{ "status": "ok" }`.
- Model runtime health: `GET http://<LLM_HOST>:<LLM_PORT>/health` (or `/v1/health`) returns HTTP `200` JSON with at least `{ "status": "ok" }`.

## Diagnostics bundle

```bash
python backend/runtime/diagnostics_bundle.py
```

The command writes a zip artifact under `backend/runtime/support_bundles/` with:

- redacted environment summary (no raw secret values)
- backend/runtime health probe snapshot
- version and git summary
- tailed runtime log files (redacted)

## TLS fronting validation

After reverse proxy + certificates are in place, validate end-to-end HTTPS with:

```bash
python backend/runtime/validate_tls_endpoint.py --base-url https://chipverify.example.com --require-hsts
```

For template config and certificate-management guidance, see `backend/runtime/tls/README.md`.

## Secret storage and rotation

Secret handling policy and operator rotation procedure are documented in:

- `backend/runtime/secret-rotation-runbook.md`

Runtime lifecycle operations (install/upgrade/rollback/incident response) are documented in:

- `backend/runtime/operations-runbook.md`

## Service templates

### Linux systemd

1. Copy `backend/runtime/systemd/chipverify-runtime.service` to `/etc/systemd/system/`.
2. Create `/etc/chipverify/runtime.env` with runtime variables (`CHIPVERIFY_GGUF_PATH`, `CHIPVERIFY_SECRET_KEY`, etc.).
3. Run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now chipverify-runtime.service
```

### Windows service (NSSM template)

1. Install NSSM and ensure `nssm.exe` is on PATH.
2. Create a service env file from `backend/runtime/windows/runtime.env.example` and save it as `backend/runtime/windows/runtime.env` (or another protected path).
3. Populate required values in that env file (`CHIPVERIFY_GGUF_PATH`, `CHIPVERIFY_SECRET_KEY`, `CHIPVERIFY_LLM_API_KEY`).
4. Run:

```powershell
.\backend\runtime\windows\install_chipverify_service.ps1 -BackendRoot "C:\chipverify\backend" -RuntimeEnvFile "C:\chipverify\backend\runtime\windows\runtime.env"
```

Optional flags:

- `-ForceReinstall` to replace an existing service with the same name.
- `-SkipPortAvailabilityCheck` if ports are intentionally in use during staged cutovers.
- `-SkipEnvInjection` only if environment is injected through another managed service policy.
- `-Mode backend-exe` only when backend and model runtime are managed as separate services.
