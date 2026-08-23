import React, { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Download, LoaderCircle, RefreshCw, Rocket, X } from "lucide-react";
import { deriveUpdateUiState, formatLastChecked } from "../../lib/desktopUpdates";
import useUpdateCelebration from "../../lib/useUpdateCelebration";
import "./UpdateGate.css";

function formatPercent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "0";
  return Math.max(0, Math.min(100, numeric)).toFixed(0);
}

function UpdateGate({ children }) {
  const [updateState, setUpdateState] = useState({
    loading: true,
    status: "idle",
    blocked: false,
  });
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");
  const [modalDismissed, setModalDismissed] = useState(false);

  const applyStatus = useCallback((status) => {
    const blocked = Boolean(
      status?.forceUpdate && status?.updateAvailable && status?.status !== "ready",
    );
    setUpdateState({ loading: false, ...status, blocked });
    if (status?.upToDate) {
      setToast(`ChipVerify ${status.currentVersion || ""} is up to date.`);
    }
  }, []);

  const loadStatus = useCallback(async () => {
    const api = window?.electronAPI;
    if (!api?.getUpdateStatus) {
      setUpdateState({ loading: false, status: "idle", blocked: false, bypassed: true });
      return;
    }

    try {
      const status = await api.getUpdateStatus();
      applyStatus(status);
    } catch (error) {
      setUpdateState({
        loading: false,
        status: "error",
        blocked: false,
        error: error?.message || "Unable to read update status.",
      });
    }
  }, [applyStatus]);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    const api = window?.electronAPI;
    if (!api?.onUpdateEvent) return undefined;

    const unsubscribe = api.onUpdateEvent((payload) => {
      if (payload?.type !== "state" || !payload?.state) return;
      const next = payload.state;
      applyStatus(next);
      if (next?.updateAvailable) {
        setModalDismissed(false);
      }
    });

    return unsubscribe;
  }, [applyStatus]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(""), 4500);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const handleCheckAgain = useCallback(async () => {
    const api = window?.electronAPI;
    if (!api?.checkForUpdates) return;
    setBusy(true);
    setToast("");
    try {
      const status = await api.checkForUpdates();
      applyStatus(status);
    } finally {
      setBusy(false);
    }
  }, [applyStatus]);

  const handleInstall = useCallback(async () => {
    const api = window?.electronAPI;
    if (!api?.installUpdate) return;
    setBusy(true);
    try {
      await api.installUpdate();
    } finally {
      setBusy(false);
    }
  }, []);

  const handleOpenDownload = useCallback(async () => {
    const api = window?.electronAPI;
    if (!api?.openUpdateDownload) return;
    await api.openUpdateDownload();
  }, []);

  const ui = deriveUpdateUiState(updateState);
  useUpdateCelebration(updateState);
  const percent = formatPercent(updateState.downloadProgress?.percent);
  const canDismissModal = ui.showModal && !updateState.blocked && !ui.downloading && !ui.ready;

  const renderModal = () => {
    if (!ui.showModal || (modalDismissed && canDismissModal)) {
      return null;
    }

    if (updateState.blocked || (updateState.updateAvailable && (ui.checking || ui.downloading || ui.ready || updateState.status === "error"))) {
      return (
        <div className="update-modal-backdrop" role="presentation">
          <div className="update-modal" role="dialog" aria-modal="true" aria-labelledby="update-modal-title">
            {canDismissModal ? (
              <button
                type="button"
                className="update-modal-close"
                aria-label="Dismiss update dialog"
                onClick={() => setModalDismissed(true)}
              >
                <X size={18} />
              </button>
            ) : null}

            <div className="update-mark">
              {ui.checking || ui.downloading ? (
                <LoaderCircle className="update-spinner" size={28} />
              ) : ui.ready ? (
                <Rocket size={28} />
              ) : (
                <Download size={28} />
              )}
            </div>

            <div className="update-eyebrow">
              {updateState.blocked ? "Required update" : "Update available"}
            </div>
            <h1 id="update-modal-title">
              {ui.ready
                ? `ChipVerify ${updateState.remoteVersion} is ready`
                : ui.downloading
                  ? `Downloading ChipVerify ${updateState.remoteVersion || ""}`
                  : ui.checking
                    ? "Checking for updates"
                    : `ChipVerify ${updateState.remoteVersion || "update"} is available`}
            </h1>

            <p>
              Installed: {updateState.currentVersion || "unknown"}
              {updateState.remoteVersion ? ` → Latest: ${updateState.remoteVersion}` : ""}
            </p>

            {ui.downloading ? (
              <div className="update-progress">
                <div className="update-progress-bar" style={{ width: `${percent}%` }} />
                <span>{percent}% complete</span>
              </div>
            ) : null}

            {updateState.releaseNotes ? (
              <div className="update-notes">{updateState.releaseNotes}</div>
            ) : null}

            {updateState.error ? (
              <div className="update-error">{updateState.error}</div>
            ) : null}

            <div className="update-actions">
              {ui.ready ? (
                <button type="button" className="primary" onClick={handleInstall} disabled={busy}>
                  {busy ? <LoaderCircle className="update-spinner" size={16} /> : "Restart and install"}
                </button>
              ) : null}
              {!ui.ready && !ui.downloading ? (
                <button type="button" onClick={handleCheckAgain} disabled={busy}>
                  {busy ? <LoaderCircle className="update-spinner" size={16} /> : <RefreshCw size={16} />}
                  Check again
                </button>
              ) : null}
              {updateState.error && updateState.manualDownloadAvailable ? (
                <button type="button" onClick={handleOpenDownload} disabled={busy}>
                  Open download page
                </button>
              ) : null}
            </div>

            <p className="update-modal-footnote">
              Last checked: {formatLastChecked(updateState.lastCheckedAt)}
            </p>
          </div>
        </div>
      );
    }

    return null;
  };

  return (
    <>
      {updateState.loading && !updateState.bypassed ? (
        <div className="update-startup-overlay" aria-live="polite">
          <div className="update-card compact">
            <LoaderCircle className="update-spinner" size={22} />
            <span>Checking for updates…</span>
          </div>
        </div>
      ) : null}

      {renderModal()}

      {toast ? (
        <div className="update-toast" role="status">
          <CheckCircle2 size={16} />
          <span>{toast}</span>
        </div>
      ) : null}

      {ui.ready && !ui.showModal ? (
        <div className="update-banner">
          <div className="update-banner-copy">
            <Rocket size={16} />
            <span>ChipVerify {updateState.remoteVersion} is ready. Restart to finish installing.</span>
          </div>
          <button type="button" onClick={handleInstall} disabled={busy}>
            {busy ? <LoaderCircle className="update-spinner" size={15} /> : "Restart Now"}
          </button>
        </div>
      ) : null}

      {updateState.blocked ? null : children}
    </>
  );
}

export default UpdateGate;
