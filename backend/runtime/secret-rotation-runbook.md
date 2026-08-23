# ChipVerify Secret Storage and Rotation Runbook (E-05)

This runbook defines where runtime secrets are stored, how often they are rotated, and how to rotate them safely.

## 1. Scope

Applies to Runtime Node deployments where backend + llama.cpp are started by:

- `backend/runtime/start_runtime_linux.sh`
- `backend/runtime/start_runtime_windows.ps1`

## 2. Secret Inventory

Required runtime secrets:

1. `CHIPVERIFY_SECRET_KEY`
   - Purpose: signs backend authentication tokens and related server-side signatures.
2. `CHIPVERIFY_LLM_API_KEY`
   - Purpose: backend-to-llama runtime authentication when `CHIPVERIFY_LLM_API_KEY_REQUIRED=true`.

Common additional secret-bearing values (environment specific):

1. `DATABASE_URL` (if it contains credentials).
2. Any organization-specific API keys injected by enterprise extensions.

## 3. Storage Policy

### Linux Runtime Node

- Store secrets outside source control, for example in `/etc/chipverify/runtime.env`.
- File ownership should be restricted to service operators.
- Suggested permissions:
  - owner: root
  - mode: `600`
- Load through systemd `EnvironmentFile` as already defined in `backend/runtime/systemd/chipverify-runtime.service`.

### Windows Runtime Node

- Store secrets in a protected machine-level environment source or enterprise secret manager integration.
- If using NSSM, inject secrets through the service environment configuration, not hardcoded scripts.
- Restrict read access to local administrators and service operators.

### Universal Rules

- Never commit secret values to git.
- Never place raw secrets in diagnostics bundles, tickets, or chat logs.
- Keep production secrets in enterprise secret manager systems when available.

## 4. Rotation Cadence

Minimum cadence (adjust to org policy if stricter):

1. `CHIPVERIFY_SECRET_KEY`: every 90 days, and immediately after suspected credential exposure.
2. `CHIPVERIFY_LLM_API_KEY`: every 60 days, and immediately after runtime-node or model-endpoint compromise concerns.
3. `DATABASE_URL` credentials: follow enterprise DB policy (commonly 60-90 days).

## 5. Pre-Rotation Checklist

1. Confirm maintenance window and operator on call.
2. Confirm latest backup of runtime env configuration exists.
3. Confirm current health is green:
   - `python backend/runtime/health_check.py`
4. Confirm you can restart runtime services (systemd/NSSM permissions).
5. Prepare rollback plan with previous secret version retained in secure vault.

## 6. Rotation Procedure (Single Runtime Node)

### Step A: Generate New Secret Values

Use a cryptographically strong generator from an approved source. Example with Python:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Generate separate values for each secret; never reuse the same value for different keys.

### Step B: Update Secret Store

1. Write new values to secret manager or protected environment file.
2. Keep previous values available for rollback until post-rotation validation completes.
3. Do not print secret values in terminal output capture.

### Step C: Restart Runtime Services

Linux systemd example:

```bash
sudo systemctl restart chipverify-runtime.service
```

Windows NSSM example:

```powershell
nssm restart ChipVerifyRuntime
```

### Step D: Post-Rotation Validation

1. Health checks:
   - `python backend/runtime/health_check.py`
2. Auth check:
   - log in through client and confirm token issuance succeeds.
3. Pipeline check:
   - run one lightweight end-to-end request.
4. Diagnostics sanity:
   - optional `python backend/runtime/diagnostics_bundle.py` and verify secrets are redacted.

## 7. Expected Impact and No-Surprise Notes

- Rotating `CHIPVERIFY_SECRET_KEY` invalidates existing bearer tokens signed with the prior value.
- Active users should re-authenticate after rotation.
- Rotating `CHIPVERIFY_LLM_API_KEY` requires runtime/backend restart so both components use the same key.
- On a single Runtime Node, plan for a short maintenance interruption during restart.

## 8. Rollback Procedure

If post-rotation validation fails:

1. Restore previous secret values from secure vault.
2. Restart runtime service.
3. Re-run health check and login test.
4. Capture diagnostics and incident notes for follow-up.

## 9. Audit Trail Requirements

Record the following for each rotation event:

1. Date/time and operator identity.
2. Secrets rotated (names only, no values).
3. Service restart timestamps.
4. Validation command outputs (redacted).
5. Rollback performed or not performed.

This evidence should be attached to enterprise change-management records.
