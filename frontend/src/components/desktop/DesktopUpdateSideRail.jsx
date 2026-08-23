import React, { useCallback, useEffect, useRef, useState } from "react";
import Icon from "../thread-first/icons";
import { playTick } from "../thread-first/useChipixSound";
import { deriveUpdateUiState } from "../../lib/desktopUpdates";
import DesktopUpdatePanel from "./DesktopUpdatePanel";
import "./UpdateGate.css";

export default function DesktopUpdateSideRail() {
  const api = typeof window !== "undefined" ? window.electronAPI : null;
  const [open, setOpen] = useState(false);
  const [updateState, setUpdateState] = useState({ status: "idle" });
  const panelRef = useRef(null);

  const syncStatus = useCallback(async () => {
    if (!api?.getUpdateStatus) return;
    try {
      const status = await api.getUpdateStatus();
      setUpdateState(status);
    } catch {
      // Ignore transient IPC failures.
    }
  }, [api]);

  useEffect(() => {
    void syncStatus();
  }, [syncStatus]);

  useEffect(() => {
    if (!api?.onUpdateEvent) return undefined;
    return api.onUpdateEvent((payload) => {
      if (payload?.type !== "state" || !payload?.state) return;
      setUpdateState(payload.state);
    });
  }, [api]);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event) => {
      if (panelRef.current?.contains(event.target)) return;
      setOpen(false);
    };
    const onKeyDown = (event) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (!api?.getUpdateStatus) {
    return null;
  }

  const ui = deriveUpdateUiState(updateState);
  const hasNotice = Boolean(updateState.updateAvailable || ui.ready);
  const badgeLabel = ui.ready ? "Update ready" : "Update available";

  const handleToggle = () => {
    playTick({ pitch: "high" });
    setOpen((previous) => !previous);
  };

  return (
    <div className="desktop-update-rail-wrap" ref={panelRef}>
      <button
        type="button"
        className={`tf-side-rail-btn desktop-update-rail-btn${open ? " active" : ""}${hasNotice ? " has-update" : ""}`}
        onClick={handleToggle}
        aria-label="Check for updates"
        aria-expanded={open}
        aria-haspopup="dialog"
        data-tip="Check for updates"
        data-tour="side-rail-updates"
      >
        <span className="tf-side-rail-ind" aria-hidden />
        <span className={`desktop-update-rail-icon${hasNotice ? " wiggle" : ""}`} aria-hidden>
          <Icon.Download width="18" height="18" />
        </span>
        {hasNotice ? (
          <span className="desktop-update-rail-badge" aria-label={badgeLabel} title={badgeLabel} />
        ) : null}
        <span className="tf-side-rail-tip">Updates</span>
      </button>

      {open ? (
        <div className="desktop-update-rail-popover" role="dialog" aria-label="Desktop updates">
          <DesktopUpdatePanel
            variant="popover"
            checkOnMount
            onClose={() => setOpen(false)}
          />
        </div>
      ) : null}
    </div>
  );
}
