import React from "react";
import Icon from "../icons";

import {
  taskStatusLabel,
} from "../taskBoardModel";

function fmtWhen(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "";
    const now = Date.now();
    const diff = now - d.getTime();
    if (diff < 60000) return "now";
    if (diff < 3600000) return `${Math.round(diff / 60000)}m`;
    if (diff < 86400000) return `${Math.round(diff / 3600000)}h`;
    return d.toLocaleDateString();
  } catch {
    return "";
  }
}

function statusDot(status) {
  const s = String(status || "").toLowerCase();
  if (s === "completed" || s === "passed") return "g";
  if (s === "failed" || s === "cancelled" || s === "interrupted") return "b";
  if (s === "running" || s === "queued") return "y";
  return "";
}

export default function ProjectDrawer({
  open,
  onClose,
  runs = [],
  artifacts = [],
  patches = [],
  threads = [],
  activeThreadId = null,
  onSwitchThread,
  onNewThread,
  onDeleteThread,
  onOpenPatch,
  onOpenRun,
  onOpenFile,
  onOpenDashboard,
  onOpenIde,
  onOpenDocs,
  onOpenSettings,
  onConfigureCadence,
  cadenceStatus = null,
  onRestartTour,
}) {
  if (!open) return null;

  const cadenceTone = cadenceStatus?.tone || "off";
  const cadenceDot = cadenceTone === "ok" ? "g" : cadenceTone === "warn" ? "y" : "b";
  const cadenceWhen =
    cadenceTone === "ok" ? "connected" : cadenceTone === "warn" ? "no license" : "set up";

  return (
    <>
      <div className="tf-drawer-overlay open" onClick={onClose} role="presentation" />
      <aside className="tf-drawer open" aria-label="History panel">
        <header className="tf-drawer-h">
          <h3>History</h3>
          <button className="x" onClick={onClose} aria-label="Close history panel">
            <Icon.Close />
          </button>
        </header>
        <div className="tf-drawer-body">
          {onSwitchThread ? (
            <div className="tf-drawer-section">
              <h4>
                Conversations
                {threads.length > 0 ? <span className="tf-drawer-badge">{threads.length}</span> : null}
              </h4>
              {onNewThread ? (
                <button className="row" onClick={onNewThread}>
                  <Icon.Plus width="14" height="14" />
                  <span className="label">Start a new conversation</span>
                </button>
              ) : null}
              {threads.length === 0 ? (
                <div className="empty">No conversations yet.</div>
              ) : null}
              {threads.slice(0, 12).map((t) => {
                const isActive = t.id === activeThreadId;
                return (
                  <div key={t.id} className={`row ${isActive ? "special" : ""}`}>
                    <button
                      type="button"
                      className="label thread-name"
                      style={{ flex: 1, textAlign: "left", background: "transparent", border: "none", cursor: "pointer", color: "inherit", padding: 0 }}
                      onClick={() => onSwitchThread(t.id)}
                      title={isActive ? "Current conversation" : "Switch to this conversation"}
                    >
                      <span className={`dot ${isActive ? "g" : ""}`} />
                      <span className="tf-drawer-thread-main">
                        <span className="tf-drawer-thread-agent">
                          {t.agent_name || t.title || "Untitled conversation"}
                        </span>
                        <span className="tf-drawer-thread-sub">
                          {t.active_task?.display_id
                            ? `${t.active_task.display_id} · ${taskStatusLabel(t.active_task.status)}`
                            : (t.title || "Conversation")}
                        </span>
                      </span>
                      {t.archived ? <span className="when">archived</span> : null}
                    </button>
                    {onDeleteThread && !isActive ? (
                      <button
                        type="button"
                        className="when"
                        style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--tf-ink-4)" }}
                        onClick={(e) => { e.stopPropagation(); onDeleteThread(t.id); }}
                        aria-label={`Delete ${t.title || "conversation"}`}
                        title="Delete conversation"
                      >
                        <Icon.Close width="12" height="12" />
                      </button>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : null}

          <div className="tf-drawer-section">
            <h4>Workspace</h4>
            {onOpenDashboard ? (
              <button className="row special" onClick={onOpenDashboard}>
                <Icon.Activity width="14" height="14" />
                <span className="label">Dashboard &amp; metrics</span>
              </button>
            ) : null}
            {onOpenIde ? (
              <button className="row" onClick={onOpenIde}>
                <Icon.Code width="14" height="14" />
                <span className="label">Open IDE workspace</span>
                <span className="when">⌘⇧E</span>
              </button>
            ) : null}
            {onOpenDocs ? (
              <button className="row" onClick={onOpenDocs}>
                <Icon.Book width="14" height="14" />
                <span className="label">Documentation</span>
              </button>
            ) : null}
            {onRestartTour ? (
              <button className="row" onClick={onRestartTour}>
                <Icon.Brain width="14" height="14" />
                <span className="label">Take the workspace tour</span>
              </button>
            ) : null}
            {onConfigureCadence ? (
              <button
                className="row"
                onClick={onConfigureCadence}
                title="Point ChipVerify at Cadence Xcelium (xrun / setup script / license)"
              >
                <span className={`dot ${cadenceDot}`} />
                <span className="label">Cadence Xcelium</span>
                <span className="when">{cadenceWhen}</span>
              </button>
            ) : null}
            {onOpenSettings ? (
              <button className="row" onClick={onOpenSettings}>
                <Icon.Settings width="14" height="14" />
                <span className="label">Settings</span>
              </button>
            ) : null}
          </div>

          <div className="tf-drawer-section">
            <h4>Patches {patches.length > 0 ? <span className="tf-drawer-badge">{patches.length}</span> : null}</h4>
            {patches.length === 0 ? <div className="empty">No pending patches.</div> : null}
            {patches.slice(0, 8).map((p) => (
              <button key={p.id} className="row" onClick={() => onOpenPatch?.(p)}>
                <span className="dot y" />
                <span className="label">{p.title || p.target_file || `Patch ${String(p.id || "").slice(0, 8)}`}</span>
                <span className="when">{fmtWhen(p.created_at)}</span>
              </button>
            ))}
          </div>

          <div className="tf-drawer-section">
            <h4>Recent runs</h4>
            {runs.length === 0 ? <div className="empty">No runs yet.</div> : null}
            {runs.slice(0, 8).map((r) => (
              <button key={r.id || r.run_id} className="row" onClick={() => onOpenRun?.(r)}>
                <span className={`dot ${statusDot(r.status)}`} />
                <span className="label">{r.title || r.summary || `Run ${String(r.id || r.run_id || "").slice(0, 8)}`}</span>
                <span className="when">{fmtWhen(r.created_at)}</span>
              </button>
            ))}
          </div>

          <div className="tf-drawer-section">
            <h4>Project files</h4>
            {artifacts.length === 0 ? <div className="empty">No files yet.</div> : null}
            {artifacts.slice(0, 30).map((a) => (
              <button key={a.id} className="row" onClick={() => onOpenFile?.(a)}>
                <Icon.File width="14" height="14" />
                <span className="label">{a.filename || a.metadata?.relative_path || "file"}</span>
              </button>
            ))}
          </div>
        </div>
      </aside>
    </>
  );
}
