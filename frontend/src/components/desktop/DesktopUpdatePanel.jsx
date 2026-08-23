import React, { useCallback, useEffect, useState } from "react";
import {
  ArrowDownCircle,
  CheckCircle2,
  Download,
  LoaderCircle,
  RefreshCw,
  Rocket,
  X,
} from "lucide-react";
import {
  deriveUpdateUiState,
  formatLastChecked,
  getUpdateStatusSummary,
} from "../../lib/desktopUpdates";
import "./UpdateGate.css";

function formatPercent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "0";
  return Math.max(0, Math.min(100, numeric)).toFixed(0);
}

export default function DesktopUpdatePanel({
  variant = "popover",
  checkOnMount = false,
  onClose,
}) {
  const api = typeof window !== "undefined" ? window.electronAPI : null;
  const [updateState, setUpdateState] = useState({ loading: true, status: "idle" });
  const [busy, setBusy] = useState(false);

  const syncStatus = useCallback(async () => {
    if (!api?.getUpdateStatus) {
      setUpdateState({ loading: false, status: "idle", bypassed: true });
      return;
    }
    try {
      const status = await api.getUpdateStatus();
      setUpdateState({ loading: false, ...status });
    } catch (error) {
      setUpdateState({
        loading: false,
        status: "error",
        error: error?.message || "Unable to read update status.",
      });
    }
  }, [api]);

  const handleCheck = useCallback(async () => {
    if (!api?.checkForUpdates) return;
    setBusy(true);
    setUpdateState((previous) => ({ ...previous, loading: true, status: "checking-remote" }));
    try {
      const status = await api.checkForUpdates();
      setUpdateState({ loading: false, ...status });
    } catch (error) {
      setUpdateState({
        loading: false,
        status: "error",
        error: error?.message || "Unable to check for updates.",
      });
    } finally {
      setBusy(false);
    }
  }, [api]);

  useEffect(() => {
    if (!checkOnMount) {
      void syncStatus();
    }
  }, [checkOnMount, syncStatus]);

  useEffect(() => {
    if (!checkOnMount) return;
    void handleCheck();
  }, [checkOnMount]); // eslint-disable-line react-hooks/exhaustive-deps -- run once when popover opens

  useEffect(() => {
    if (!api?.onUpdateEvent) return undefined;
    return api.onUpdateEvent((payload) => {
      if (payload?.type !== "state" || !payload?.state) return;
      setUpdateState((previous) => ({ ...previous, loading: false, ...payload.state }));
    });
  }, [api]);

  const handleInstall = useCallback(async () => {
    if (!api?.installUpdate) return;
    setBusy(true);
    try {
      await api.installUpdate();
    } finally {
      setBusy(false);
    }
  }, [api]);

  const handleOpenDownload = useCallback(async () => {
    if (!api?.openUpdateDownload) return;
    await api.openUpdateDownload();
  }, [api]);

  if (!api?.getUpdateStatus) {
    return null;
  }

  const ui = deriveUpdateUiState(updateState);
  const summary = getUpdateStatusSummary(updateState, ui);
  const percent = formatPercent(updateState.downloadProgress?.percent);
  const latestLabel = updateState.remoteVersion || (ui.checking || updateState.loading ? "…" : "—");
  const showPrimaryInstall = ui.ready;
  const showPrimaryCheck = !ui.ready && !ui.downloading;

  return (
    <div className={`desktop-update-panel desktop-update-panel--${variant}`}>
      <div className="desktop-update-panel-head">
        <div className="desktop-update-panel-title">
          <span className={`desktop-update-panel-icon tone-${summary.tone}`}>
            {summary.tone === "checking" || summary.tone === "downloading" ? (
              <LoaderCircle className="update-spinner" size={16} />
            ) : summary.tone === "ready" ? (
              <Rocket size={16} />
            ) : summary.tone === "available" ? (
              <ArrowDownCircle size={16} />
            ) : summary.tone === "error" ? (
              <Download size={16} />
            ) : (
              <CheckCircle2 size={16} />
            )}
          </span>
          <div>
            <h4>Updates</h4>
            <p>{summary.title}</p>
          </div>
        </div>
        {onClose ? (
          <button type="button" className="desktop-update-panel-close" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        ) : null}
      </div>

      <div className={`desktop-update-summary tone-${summary.tone}`}>
        <p>{summary.message}</p>
      </div>

      <div className="desktop-update-version-grid">
        <div className="desktop-update-version-card">
          <span className="desktop-update-label">Installed</span>
          <strong>{updateState.currentVersion || "unknown"}</strong>
        </div>
        <div className="desktop-update-version-card">
          <span className="desktop-update-label">Latest</span>
          <strong className={updateState.updateAvailable ? "is-new" : ""}>{latestLabel}</strong>
        </div>
      </div>

      {ui.downloading ? (
        <div className="desktop-update-progress-block">
          <div className="desktop-update-progress-track">
            <div className="desktop-update-progress-fill" style={{ width: `${percent}%` }} />
          </div>
          <span>{percent}% downloaded</span>
        </div>
      ) : null}

      <div className="desktop-update-panel-actions">
        {showPrimaryInstall ? (
          <button type="button" className="primary" onClick={handleInstall} disabled={busy}>
            {busy ? <LoaderCircle className="update-spinner" size={14} /> : <Rocket size={14} />}
            Restart to update
          </button>
        ) : null}
        {showPrimaryCheck ? (
          <button
            type="button"
            className="secondary"
            onClick={handleCheck}
            disabled={busy || ui.checking}
          >
            {busy || ui.checking ? <LoaderCircle className="update-spinner" size={14} /> : <RefreshCw size={14} />}
            Check for updates
          </button>
        ) : null}
        {updateState.error && updateState.manualDownloadAvailable ? (
          <button type="button" onClick={handleOpenDownload} disabled={busy}>
            Open download page
          </button>
        ) : null}
      </div>

      <p className="desktop-update-footnote">
        Last checked {formatLastChecked(updateState.lastCheckedAt, { relative: true })}
      </p>
    </div>
  );
}
