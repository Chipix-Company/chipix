import React, { useEffect, useState } from "react";
import {
  getXceliumConfig,
  clearXceliumConfig,
  testCadenceIntegration,
} from "../../../services/edaWorkspaceApi";

/**
 * CadenceConfigModal — manually point ChipVerify at Cadence Xcelium when
 * auto-detection fails.
 */

const STATUS_TONE = {
  ready: { tone: "ok", text: "Detection — xrun and license env are configured." },
  no_license: { tone: "warn", text: "Detection — xrun found, but no license is configured yet." },
  not_installed: { tone: "off", text: "Detection — xrun is still not found." },
  unsupported_platform: { tone: "off", text: "Detection — Cadence Xcelium requires a Linux host." },
};

const INTEGRATION_TONE = {
  passed: { tone: "ok", text: "Cadence verification passed on the FIFO fixture." },
  failed: { tone: "off", text: "Cadence verification failed on the FIFO fixture." },
  blocked: { tone: "warn", text: "Cadence verification blocked — xrun is not reachable." },
  error: { tone: "off", text: "Cadence verification could not run." },
  not_run: { tone: "warn", text: "Cadence verification did not run — only detection was checked." },
};

function StatusBanner({ detection }) {
  if (!detection) return null;
  const status = detection?.details?.status || (detection?.available ? "ready" : "not_installed");
  const view = STATUS_TONE[status] || STATUS_TONE.not_installed;
  return (
    <div className={`tf-cadence-banner tone-${view.tone}`}>
      <span className="tf-cadence-banner-dot" aria-hidden />
      <div className="tf-cadence-banner-body">
        <strong>{view.text}</strong>
        <span className="tf-cadence-banner-guidance">
          Path and license probe only — real xrun commands run in the verification section below.
        </span>
        {detection?.path ? <span className="tf-cadence-banner-path">{detection.path}</span> : null}
        {!detection?.available && detection?.guidance ? (
          <span className="tf-cadence-banner-guidance">{detection.guidance}</span>
        ) : null}
      </div>
    </div>
  );
}

function stepState(step) {
  if (step?.passed) return "done";
  if (step?.returncode != null || step?.timed_out) return "bad";
  return "todo";
}

function IntegrationRunReport({ integrationTest }) {
  const summary = integrationTest?.run_summary || {};
  const steps = summary?.steps || [];
  if (!steps.length) return null;

  const regression = summary?.regression || integrationTest?.runner?.regression || {};
  const realExecution = summary?.real_execution;

  return (
    <div className="tf-cadence-run-report">
      <div className="tf-cadence-run-report-head">
        <strong>Cadence verification run</strong>
        <span className={`tf-cadence-run-report-badge ${realExecution ? "ok" : "bad"}`}>
          {realExecution ? "xrun executed" : "xrun not fully verified"}
        </span>
      </div>
      {summary?.summary ? (
        <p className="tf-cadence-run-report-summary">{summary.summary}</p>
      ) : null}
      {(regression?.passed != null || regression?.failed != null) ? (
        <p className="tf-cadence-run-report-meta">
          UVM tests: {regression.passed || 0} passed · {regression.failed || 0} failed
        </p>
      ) : null}
      <div className="tf-cadence-run-phases" role="list" aria-label="Cadence xrun commands">
        {steps.filter((step) => step.uses_xrun).map((step) => (
          <div
            key={step.phase}
            className={`tf-cadence-run-phase ${stepState(step)}`}
            role="listitem"
          >
            <span className="mark">{step.passed ? "✓" : step.returncode != null ? "✕" : "…"}</span>
            <span className="name">{step.label}</span>
            {step.returncode != null ? <span className="rc">rc {step.returncode}</span> : null}
          </div>
        ))}
      </div>
      {steps.filter((step) => step.uses_xrun).map((step) => (
        <details key={`${step.phase}-detail`} className="tf-cadence-log-details">
          <summary>{step.label} — xrun command and output</summary>
          {step.command ? (
            <pre className="tf-cadence-log-pre tf-cadence-command-pre">{step.command}</pre>
          ) : null}
          {step.log_excerpt ? (
            <pre className="tf-cadence-log-pre">{step.log_excerpt}</pre>
          ) : (
            <p className="tf-cadence-banner-guidance">No log output captured for this phase.</p>
          )}
        </details>
      ))}
      {summary?.work_dir ? (
        <p className="tf-cadence-run-report-meta">Run directory: {summary.work_dir}</p>
      ) : null}
    </div>
  );
}

function IntegrationBanner({ integrationTest, testAttempted, requestError }) {
  if (testAttempted && !integrationTest && requestError) {
    const view = INTEGRATION_TONE.not_run;
    return (
      <div className={`tf-cadence-banner tone-${view.tone}`}>
        <span className="tf-cadence-banner-dot" aria-hidden />
        <div className="tf-cadence-banner-body">
          <strong>{view.text}</strong>
          <span className="tf-cadence-banner-guidance">
            Your Cadence settings may still be saved, but the FIFO verification flow did not finish.
            {" "}{requestError}
          </span>
        </div>
      </div>
    );
  }
  if (!integrationTest) return null;

  const view = INTEGRATION_TONE[integrationTest.status] || INTEGRATION_TONE.failed;
  const method = integrationTest?.compile_gate?.method;
  const duration = integrationTest?.duration_ms
    ? `${Math.round(integrationTest.duration_ms / 1000)}s`
    : null;
  const xrunCount = integrationTest?.run_summary?.xrun_invocations;

  return (
    <>
      <div className={`tf-cadence-banner tone-${view.tone}`}>
        <span className="tf-cadence-banner-dot" aria-hidden />
        <div className="tf-cadence-banner-body">
          <strong>{integrationTest.message || view.text}</strong>
          {method ? <span className="tf-cadence-banner-path">Method: {method}</span> : null}
          {xrunCount != null ? (
            <span className="tf-cadence-banner-path">xrun invocations: {xrunCount}</span>
          ) : null}
          {duration ? <span className="tf-cadence-banner-guidance">Completed in {duration}</span> : null}
          {integrationTest.status === "failed" && integrationTest?.compile_gate?.errors?.length ? (
            <span className="tf-cadence-banner-guidance">
              {integrationTest.compile_gate.errors[0]}
            </span>
          ) : null}
        </div>
      </div>
      <IntegrationRunReport integrationTest={integrationTest} />
    </>
  );
}

export default function CadenceConfigModal({
  open,
  onClose,
  authToken,
  detail = null,
  onSaved,
}) {
  const [xrunBin, setXrunBin] = useState("");
  const [setupScript, setSetupScript] = useState("");
  const [setupShell, setSetupShell] = useState("");
  const [license, setLicense] = useState("");
  const [detection, setDetection] = useState(detail);
  const [integrationTest, setIntegrationTest] = useState(null);
  const [testAttempted, setTestAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setError("");
    setDetection(detail);
    setIntegrationTest(null);
    setTestAttempted(false);
    getXceliumConfig(authToken)
      .then((r) => {
        const c = r?.config || {};
        setXrunBin(c.xrun_bin || "");
        setSetupScript(c.setup_script || "");
        setSetupShell(c.setup_shell || "");
        setLicense(c.license || "");
      })
      .catch(() => {});
  }, [open, authToken, detail]);

  if (!open) return null;

  const save = () => {
    setBusy(true);
    setError("");
    setIntegrationTest(null);
    setTestAttempted(true);
    testCadenceIntegration(
      {
        xrunBin: xrunBin.trim(),
        setupScript: setupScript.trim(),
        setupShell: setupShell.trim(),
        license: license.trim(),
      },
      authToken,
    )
      .then((r) => {
        setDetection(r?.detection || null);
        setIntegrationTest(r || null);
        onSaved?.(r?.detection || null);
      })
      .catch((e) => {
        setIntegrationTest(null);
        setError(e?.message || "Could not apply Cadence settings.");
      })
      .finally(() => setBusy(false));
  };

  const reset = () => {
    setBusy(true);
    setError("");
    setTestAttempted(false);
    clearXceliumConfig(authToken)
      .then((r) => {
        setXrunBin("");
        setSetupScript("");
        setSetupShell("");
        setLicense("");
        setDetection(r?.detection || null);
        setIntegrationTest(null);
        onSaved?.(r?.detection || null);
      })
      .catch((e) => setError(e?.message || "Could not clear Cadence settings."))
      .finally(() => setBusy(false));
  };

  return (
    <div className="tf-cadence-overlay" role="dialog" aria-modal="true" aria-label="Configure Cadence Xcelium">
      <button type="button" className="tf-cadence-backdrop" aria-label="Close" onClick={onClose} />
      <div className="tf-cadence-panel">
        <div className="tf-cadence-head">
          <h3 className="tf-cadence-title">Connect Cadence Xcelium</h3>
          <p className="tf-cadence-sub">
            Cadence is usually launched from a sourced lab script (e.g. <code>cshrc_kle</code>).
            Apply &amp; test saves your config, materializes the predefined FIFO UVM fixture, and runs it
            through the same Cadence verification flow as production: xrun compile → elaborate → simulate.
            Pass means xrun actually ran and returned output for each phase.
          </p>
        </div>

        <StatusBanner detection={detection} />
        <IntegrationBanner
          integrationTest={integrationTest}
          testAttempted={testAttempted}
          requestError={error}
        />

        <div className="tf-cadence-form">
          <label className="tf-cadence-field">
            <span className="tf-cadence-label">Lab setup script</span>
            <input
              type="text"
              className="tf-cadence-input"
              placeholder="/opt/site/cshrc_kle"
              value={setupScript}
              onChange={(e) => setSetupScript(e.target.value)}
            />
            <span className="tf-cadence-hint">Sourced to import PATH, libraries, and the license. Recommended.</span>
          </label>

          <div className="tf-cadence-row">
            <label className="tf-cadence-field grow">
              <span className="tf-cadence-label">xrun path (optional)</span>
              <input
                type="text"
                className="tf-cadence-input"
                placeholder="/opt/cadence/xcelium/tools/bin/xrun"
                value={xrunBin}
                onChange={(e) => setXrunBin(e.target.value)}
              />
              <span className="tf-cadence-hint">Use if you prefer pointing straight at the binary.</span>
            </label>
            <label className="tf-cadence-field shell">
              <span className="tf-cadence-label">Script shell</span>
              <select
                className="tf-cadence-input"
                value={setupShell}
                onChange={(e) => setSetupShell(e.target.value)}
              >
                <option value="">Auto</option>
                <option value="tcsh">tcsh</option>
                <option value="csh">csh</option>
                <option value="bash">bash</option>
                <option value="sh">sh</option>
              </select>
            </label>
          </div>

          <label className="tf-cadence-field">
            <span className="tf-cadence-label">License (optional)</span>
            <input
              type="text"
              className="tf-cadence-input"
              placeholder="5280@license-server.edu"
              value={license}
              onChange={(e) => setLicense(e.target.value)}
            />
            <span className="tf-cadence-hint">Sets CDS_LIC_FILE. Skip if the setup script already does.</span>
          </label>
        </div>

        {error && integrationTest ? <div className="tf-cadence-error">{error}</div> : null}

        <div className="tf-cadence-actions">
          <button type="button" className="tf-btn ghost" onClick={reset} disabled={busy}>
            Clear
          </button>
          <div className="tf-cadence-actions-right">
            <button type="button" className="tf-btn ghost" onClick={onClose} disabled={busy}>
              Close
            </button>
            <button type="button" className="tf-btn primary" onClick={save} disabled={busy}>
              {busy ? "Running Cadence verification…" : "Apply & test"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
