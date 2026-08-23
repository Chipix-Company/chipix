# Simulator Plugins

This folder contains the first backend-owned simulator plugin layer for Chipix.
It is intentionally separate from the existing staged verification generator so
Xcelium support can grow without destabilizing the current UVM/Formal/UnitSim
workflow.

## Implemented Scope

- Xcelium detection through `xrun`, version probing, UVM help probing, and
  license environment visibility.
- Per-run sandbox creation under the project outputs directory.
- Generated-file copy plus read-only RTL link/copy strategy with checksum
  integrity checks.
- Large-log pipeline: filter, parse, cluster, and assemble structured
  diagnostics.
- Lightweight VCD reader for signal snapshots and X/Z detection.
- Spec-grounded evidence bundle assembly from mental-model requirements, RTL
  references, logs, and waveforms.
- Bounded repair-and-rerun loop for generated simulator collateral. Compile
  failures are parsed, safe generated-file repairs are applied inside the
  sandbox, and Xcelium is retried up to the configured repair limit.
- Feedback memory persisted under `results/feedback_memory.json` so later
  agent steps can see root causes, applied fixes, and evidence verdicts.
- Coverage text parsing and sign-off style closure reports.
- API endpoints under `/api/v1/tools/simulators` and
  `/api/v1/projects/{project_id}/simulate`.

## Cadence Environment (lab / university installs)

Cadence tools are not normal OS packages: they live under a large install tree
and only work after a site setup script is sourced (e.g. `source cshrc_kle` in
tcsh), which sets `PATH`, `LD_LIBRARY_PATH`, and the license variables
(`CDS_LIC_FILE` / `LM_LICENSE_FILE`). The ChipVerify backend is launched from a
desktop/Electron session that never sourced that script, so `xrun` and the
license are invisible and Cadence shows up as "off".

To bridge this without a wrapper script, set:

| Variable | Purpose |
|----------|---------|
| `CHIPVERIFY_CADENCE_SETUP` | Path to the lab setup script (e.g. `/opt/site/cshrc_kle`). The backend sources it once, in the correct shell, and imports the resulting Cadence env (PATH, libs, license, EDA vars) before detection. |
| `CHIPVERIFY_CADENCE_SETUP_SHELL` | Optional shell override (`csh`/`tcsh`/`bash`/`sh`). Defaults to tcsh for csh-style scripts (name contains `csh`), otherwise sh. |
| `CHIPVERIFY_XCELIUM_BIN` | Direct path to `xrun`, bypassing PATH discovery. |
| `CDS_LIC_FILE` / `LM_LICENSE_FILE` | License server `port@host`, if set manually instead of via the setup script. |

The import is conservative: it pulls in `PATH`/`LD_LIBRARY_PATH` (already merged
on top of the inherited values by the sourced shell), EDA/license variables, and
brand-new site-specific variables — but never overwrites the backend's own
Python runtime variables. These can also be placed in the bundled backend
`.env` file (`CHIPVERIFY_ENV_FILE`). `detect()`/`refresh()` exposes
`details.setup_script` and `details.setup_applied` so the UI can confirm what was
imported.

### Manual override at runtime (when auto-detection fails)

The environment above is the **default**. When Cadence still isn't found, a user
can point ChipVerify at it from the running app — no restart, no relaunch with a
different env. The override is validated, persisted to
`outputs/cadence_config.json` (override the path with `CHIPVERIFY_CADENCE_CONFIG`),
and re-applied on every detection so it survives a backend restart. A manual
value wins while set; clearing a field reverts that variable to its launch-time
default.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/v1/tools/simulators/xcelium/config` | Read the current manual override. |
| `POST /api/v1/tools/simulators/xcelium/configure` | Set `xrun_bin` / `setup_script` / `setup_shell` / `license` and get back a fresh detection result. Paths are validated (a bad path returns 400). `null` leaves a field unchanged; `""` clears it. |
| `DELETE /api/v1/tools/simulators/xcelium/config` | Remove the override and revert to launch defaults. |

Each saved field maps onto the same environment variable detection already
honors (`xrun_bin`→`CHIPVERIFY_XCELIUM_BIN`,
`setup_script`→`CHIPVERIFY_CADENCE_SETUP`,
`setup_shell`→`CHIPVERIFY_CADENCE_SETUP_SHELL`, `license`→`CDS_LIC_FILE`), so the
manual path and the env-var path stay in sync.

## Current Limitations

- On Windows, Xcelium is reported as unavailable. The plugin still supports
  mock logs and uploaded-log analysis for development.
- Auto-repair is intentionally limited to generated files in the simulator
  sandbox. Original RTL is never edited automatically.
- Coverage parsing expects text reports. IMC invocation can be added once the
  Linux/Xcelium runner is wired in production.
- Frontend controls are not added yet; callers can use the API or VS Code
  extension surface.
