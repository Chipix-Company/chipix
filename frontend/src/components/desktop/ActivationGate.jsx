import React, { useCallback, useEffect, useState } from "react";
import { AnalyticsEvents, track } from "../../lib/observability";
import { CheckCircle2, Copy, KeyRound, LoaderCircle, ShieldCheck } from "lucide-react";
import "./ActivationGate.css";

function ActivationGate({ children }) {
  const [status, setStatus] = useState({ loading: true });
  const [machineKey, setMachineKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const loadStatus = useCallback(async () => {
    const api = window?.electronAPI;
    if (!api?.getActivationStatus) {
      setStatus({ loading: false, activated: true, requiresActivation: false, bypassed: true });
      return;
    }

    try {
      const nextStatus = await api.getActivationStatus();
      setStatus({ loading: false, ...nextStatus });
    } catch (err) {
      setStatus({ loading: false, activated: false, requiresActivation: true });
      setError(err?.message || "Unable to read activation status.");
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const handleCopyMachineId = useCallback(async () => {
    if (!status.machineId) return;
    try {
      await navigator.clipboard.writeText(status.machineId);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  }, [status.machineId]);

  const handleActivate = useCallback(async (event) => {
    event.preventDefault();
    setError("");
    setBusy(true);

    try {
      const result = await window.electronAPI.activateMachine({
        machineKey,
      });
      if (!result?.activated) {
        setError(result?.message || "Machine key could not be verified.");
        setStatus((previous) => ({ ...previous, ...result, loading: false }));
        return;
      }

      setStatus({ loading: false, ...result });
      track(AnalyticsEvents.ACTIVATION_COMPLETED, {
        machine_id: result?.machineId,
      });
    } catch (err) {
      setError(err?.message || "Activation failed.");
    } finally {
      setBusy(false);
    }
  }, [machineKey]);

  if (status.loading) {
    return (
      <div className="activation-shell">
        <div className="activation-card compact">
          <LoaderCircle className="activation-spinner" size={22} />
          <span>Checking desktop activation...</span>
        </div>
      </div>
    );
  }

  if (status.activated || !status.requiresActivation) {
    return children;
  }

  return (
    <div className="activation-shell">
      <form className="activation-card" onSubmit={handleActivate}>
        <div className="activation-mark">
          <ShieldCheck size={26} />
        </div>
        <div>
          <div className="activation-eyebrow">ChipVerify Desktop</div>
          <h1>Activate this machine</h1>
          <p>
            Send this Machine ID to the ChipVerify owner, then enter the Machine Key they give you.
            The demo runtime is already configured with the owner-managed model provider.
          </p>
        </div>

        <label className="activation-field">
          <span>Machine ID</span>
          <div className="activation-copy-row">
            <input value={status.machineId || ""} readOnly />
            <button type="button" onClick={handleCopyMachineId} aria-label="Copy Machine ID">
              {copied ? <CheckCircle2 size={16} /> : <Copy size={16} />}
            </button>
          </div>
        </label>

        <label className="activation-field">
          <span>Machine Key</span>
          <div className="activation-key-row">
            <KeyRound size={17} />
            <input
              value={machineKey}
              onChange={(event) => setMachineKey(event.target.value)}
              placeholder="CVK-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX"
              autoFocus
            />
          </div>
        </label>

        {status.managedBackendAvailable === false ? (
          <div className="activation-warning">
            Packaged backend runtime was not found. The app can activate, but it will need an external backend URL.
          </div>
        ) : null}

        {error ? <div className="activation-error">{error}</div> : null}

        <button className="activation-primary" type="submit" disabled={busy || !machineKey.trim()}>
          {busy ? <LoaderCircle className="activation-spinner" size={17} /> : <ShieldCheck size={17} />}
          Activate and Open Workspace
        </button>
      </form>
    </div>
  );
}

export default ActivationGate;
