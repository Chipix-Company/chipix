# ChipVerify Runtime Node Operations Runbook (H-03)

This runbook is for enterprise operators managing the Runtime Node (backend + local llama runtime).

## 1. Operating Model

- Runtime Node hosts:
  - backend API
  - llama.cpp server binary
  - approved GGUF model artifacts
- Client laptops connect to Runtime Node over HTTPS (reverse-proxy fronting recommended).
- Runtime services should run under service supervision (systemd on Linux, NSSM on Windows).

## 2. Prerequisites

1. Approved runtime artifacts are available:
   - llama-server binary
   - GGUF model file
   - sha256 manifest (recommended)
2. Runtime environment variables are prepared:
   - required: `CHIPVERIFY_GGUF_PATH`, `CHIPVERIFY_SECRET_KEY`
   - required by default policy: `CHIPVERIFY_LLM_API_KEY`
3. Python dependencies are installed from `backend/requirements.txt`.
4. Network/firewall policy allows intended client-to-proxy access.
5. Service account has least-privilege access to runtime files and logs.

## 3. Baseline Directory and Files

- Launcher scripts:
  - `backend/runtime/start_runtime_linux.sh`
  - `backend/runtime/start_runtime_windows.ps1`
- Health probe:
  - `backend/runtime/health_check.py`
- Diagnostics bundle:
  - `backend/runtime/diagnostics_bundle.py`
- Artifact verification:
  - `backend/runtime/verify_artifacts.py`
- TLS fronting templates:
  - `backend/runtime/tls/README.md`
- Secret rotation runbook:
  - `backend/runtime/secret-rotation-runbook.md`

## 4. Initial Install Procedure

### Linux (primary production path)

1. Place runtime files under a controlled path (for example `/opt/chipverify/backend`).
2. Create runtime env file (`/etc/chipverify/runtime.env`) with required values.
3. Optional but recommended: set `CHIPVERIFY_ARTIFACT_MANIFEST_PATH`.
4. Install service unit from `backend/runtime/systemd/chipverify-runtime.service`.
5. Enable and start service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now chipverify-runtime.service
```

6. Validate service health:

```bash
python backend/runtime/health_check.py
```

### Windows (test/lab path)

1. Place runtime files under a controlled path (for example `C:\chipverify\backend`).
2. Create a protected runtime env file from `backend/runtime/windows/runtime.env.example`.
3. Set required values in that env file (`CHIPVERIFY_GGUF_PATH`, `CHIPVERIFY_SECRET_KEY`, `CHIPVERIFY_LLM_API_KEY`).
4. Install service with NSSM template:

```powershell
.\backend\runtime\windows\install_chipverify_service.ps1 -BackendRoot "C:\chipverify\backend" -RuntimeEnvFile "C:\chipverify\backend\runtime\windows\runtime.env"
nssm start ChipVerifyRuntime
```

Default mode is `runtime-script` and should be used for most deployments because it starts backend (`7348`) and model runtime (`7349`) together under one service.

Use `-Mode backend-exe` only when backend and model runtime are intentionally split into separate services. In that mode, keep these values explicit in the service env file:

- `CHIPVERIFY_LLM_PROVIDER=local`
- `CHIPVERIFY_LLM_BASE_URL=http://127.0.0.1:7349/v1`
- `CHIPVERIFY_LLM_API_KEY` aligned with the separately managed model runtime

If backend bind values differ from defaults, update desktop managed policy endpoint (`CHIPVERIFY_BACKEND_URL`) to match.

5. Validate service health:

```powershell
python backend/runtime/health_check.py
```

## 5. Day-2 Health Checks

Run these checks at start of shift and after any config change:

1. Runtime health probe:
   - `python backend/runtime/health_check.py`
2. Service status:
   - Linux: `systemctl status chipverify-runtime.service`
   - Windows: `nssm status ChipVerifyRuntime`
3. Log sanity:
   - Confirm no repeated auth failures, model-missing errors, or restart loops.

## 6. Upgrade Procedure

Use this flow for backend/runtime/model updates.

1. Announce maintenance window.
2. Stage new artifacts in versioned directory.
3. If using manifest verification, update manifest with new sha256 values.
4. Stop service:
   - Linux: `sudo systemctl stop chipverify-runtime.service`
   - Windows: `nssm stop ChipVerifyRuntime`
5. Deploy updated files/config.
6. Start service:
   - Linux: `sudo systemctl start chipverify-runtime.service`
   - Windows: `nssm start ChipVerifyRuntime`
7. Validate:
   - `python backend/runtime/health_check.py`
   - run one controlled end-to-end request from client app.
8. Record change evidence (commands, versions, health output, operator name).

## 7. Rollback Procedure

If upgrade validation fails:

1. Stop service.
2. Restore previous known-good package and environment file values.
3. Start service with prior version.
4. Re-run health probe and a small client workflow.
5. Capture diagnostics bundle and attach to incident/change record.

## 8. Diagnostics and Support Handoff

When an incident occurs:

1. Collect runtime diagnostics:

```bash
python backend/runtime/diagnostics_bundle.py
```

2. Save bundle path and timestamp.
3. Confirm secrets are redacted before sharing.
4. Attach bundle + service logs + recent config diff to support ticket.

## 9. Incident Response Playbooks

### Runtime unavailable

1. Check service status and restart count.
2. Inspect launcher logs for startup failure causes.
3. Validate binary and GGUF paths.
4. Re-run health probe after corrective action.

### Auth mismatch between backend and llama runtime

1. Confirm `CHIPVERIFY_LLM_API_KEY` value is set and consistent.
2. Restart service after synchronizing key values.
3. Validate health and one generation request.

### Model missing or wrong path

1. Confirm `CHIPVERIFY_GGUF_PATH` points to existing approved model.
2. If manifest enforcement is enabled, confirm hash values.
3. Restart service and validate health.

### Timeouts / performance degradation

1. Check queue depth and server utilization.
2. Review context size and parallel settings (`CHIPVERIFY_LLM_CONTEXT_SIZE`, `CHIPVERIFY_LLM_PARALLEL`).
3. Reduce concurrent load or scale to additional runtime node per capacity policy.

## 10. Security Operations Requirements

1. Keep backend and model runtime bound to loopback unless explicitly approved.
2. Keep wildcard CORS disabled in production.
3. Use HTTPS reverse-proxy fronting for multi-host deployments and validate with:

```bash
python backend/runtime/validate_tls_endpoint.py --base-url https://<runtime-host> --require-hsts
```

4. Rotate secrets according to `backend/runtime/secret-rotation-runbook.md`.

## 11. Evidence Checklist Per Change

Record all of the following:

1. Change ID, date/time, and operator.
2. Artifact versions and hashes (if changed).
3. Service restart commands executed.
4. Health-check output.
5. Client workflow smoke-test result.
6. Rollback status (performed or not).

This checklist is required for compliance-grade deployments.
