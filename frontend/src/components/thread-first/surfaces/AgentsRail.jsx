import React, { useMemo, useState } from "react";
import Icon from "../icons";

/**
 * Project agents rail — matches mock `ide-right` "Project agents" block:
 * section header + flat `.ai-msg` rows: **Name** · status (see mock HTML).
 */

function statusAfterBullet(agent) {
  const raw = String(agent.status || agent.run_status || "idle").trim();
  if (!raw) return "idle";
  if (raw.length <= 56) return raw;
  return `${raw.slice(0, 53)}…`;
}

export default function AgentsRail({ agents, runStatus, runId }) {
  const isActive = String(runStatus || "").toLowerCase() === "running";
  const [open, setOpen] = useState(isActive);

  const list = useMemo(() => (Array.isArray(agents) ? agents : []), [agents]);

  const summary = useMemo(() => {
    if (!list.length) return isActive ? "Starting…" : "Idle";
    const busy = list.filter((a) => {
      const t = `${a.status || ""} ${a.last_event_phase || ""}`.toLowerCase();
      return /run|active|busy|simulat|verif|work/.test(t);
    }).length;
    if (busy) return `${busy} active`;
    return "All idle";
  }, [list, isActive]);

  return (
    <div className={`tf-ide-agents ${open ? "open" : ""}`}>
      <button type="button" className="grp grp-toggle" onClick={() => setOpen((v) => !v)}>
        <span className="caret" aria-hidden>
          {open ? <Icon.ChevronDown width="10" height="10" /> : <Icon.ChevronRight width="10" height="10" />}
        </span>
        <span className="grp-lbl">Project agents</span>
        <span className="sub">{summary}</span>
        {isActive ? <span className="live" aria-hidden /> : null}
      </button>
      {open ? (
        <div className="tf-ide-agents-rows">
          {list.length === 0 ? (
            <div className="tf-ai-msg tf-ide-agents-empty">
              {isActive
                ? "Waiting for the pipeline to report in…"
                : "Agent status updates here when you run verification on this project."}
            </div>
          ) : (
            list.map((agent) => {
              const name = agent.name || agent.id || "Agent";
              const line = statusAfterBullet(agent);
              const detail = agent.last_event_message
                ? String(agent.last_event_message).trim()
                : "";
              return (
                <div
                  key={agent.id || name}
                  className="tf-ai-msg tf-ide-agent-line"
                >
                  <div className="tf-ide-agent-line-main">
                    <strong>{name}</strong>
                    <span className="tf-ide-agent-sep"> · </span>
                    <span className="tf-ide-agent-status">{line}</span>
                  </div>
                  {detail ? (
                    <div className="tf-ide-agent-detail">{detail}</div>
                  ) : null}
                </div>
              );
            })
          )}
          {runId ? (
            <div className="tf-ide-agents-foot">
              Run <span className="tf-mono">{String(runId).slice(0, 8)}</span>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
