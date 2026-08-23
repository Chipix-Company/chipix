import React, { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import Icon from "../icons";
import useOverlayClose from "../useOverlayClose";
import { playTick } from "../useChipixSound";
import {
  codebaseGraphHtmlUrl,
  fetchCodebaseGraphReport,
  fetchCodebaseGraphStatus,
  rebuildCodebaseGraph,
} from "../../../api/codebaseGraphApi";

const TABS = [
  { id: "graph", label: "Graph" },
  { id: "report", label: "Report" },
];

export default function CodebaseGraphOverlay({
  open,
  onClose,
  projectId,
  authToken,
  projectName,
  onStatusChange,
}) {
  const [tab, setTab] = useState("graph");
  const [status, setStatus] = useState(null);
  const [report, setReport] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useOverlayClose(open, onClose);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError("");
    try {
      const st = await fetchCodebaseGraphStatus(projectId, authToken);
      setStatus(st);
      onStatusChange?.(st);
      if (st.status === "ready") {
        try {
          const md = await fetchCodebaseGraphReport(projectId, authToken);
          setReport(md);
        } catch {
          setReport(st.report_excerpt || "");
        }
      }
    } catch (e) {
      setError(e.message || "Failed to load codebase graph");
    } finally {
      setLoading(false);
    }
  }, [projectId, authToken, onStatusChange]);

  useEffect(() => {
    if (!open || !projectId) return undefined;
    void refresh();
    const id = setInterval(() => {
      if (status?.status === "building") void refresh();
    }, 4000);
    return () => clearInterval(id);
  }, [open, projectId, refresh, status?.status]);

  const iframeSrc = useMemo(() => {
    if (!projectId || status?.status !== "ready") return null;
    return codebaseGraphHtmlUrl(projectId, authToken);
  }, [projectId, authToken, status?.status]);

  const handleRebuild = async () => {
    playTick({ pitch: "high" });
    try {
      await rebuildCodebaseGraph(projectId, authToken);
      await refresh();
    } catch (e) {
      setError(e.message || "Rebuild failed");
    }
  };

  if (!open) return null;

  return (
    <div className="tf-overlay tf-mm-overlay" role="dialog" aria-label="Codebase graph">
      <div className="tf-overlay-backdrop" onClick={onClose} aria-hidden />
      <div className="tf-overlay-panel tf-mm-panel">
        <header className="tf-overlay-head">
          <div>
            <div className="tf-overlay-title">Codebase map</div>
            <div className="tf-overlay-sub">
              {projectName || "Project"} · structural graph from RTL + spec
            </div>
          </div>
          <div className="tf-overlay-actions">
            <button type="button" className="micro-btn" onClick={() => void refresh()} disabled={loading}>
              Refresh
            </button>
            <button type="button" className="micro-btn" onClick={() => void handleRebuild()}>
              Rebuild
            </button>
            <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
              <Icon.Close />
            </button>
          </div>
        </header>

        <div className="tf-mm-tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`tf-mm-tab ${tab === t.id ? "active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="tf-mm-graph-wrap" style={{ minHeight: "60vh" }}>
          {error ? <div className="tf-empty-state">{error}</div> : null}
          {!error && status?.status === "building" ? (
            <div className="tf-empty-state">
              Mapping codebase… {status.progress || ""}
            </div>
          ) : null}
          {!error && status?.status !== "ready" && status?.status !== "building" ? (
            <div className="tf-empty-state">
              Upload RTL or spec artifacts to build the codebase map.
            </div>
          ) : null}
          {tab === "graph" && iframeSrc ? (
            <iframe
              title="Codebase graph"
              src={iframeSrc}
              className="tf-codebase-graph-frame"
              style={{ width: "100%", height: "65vh", border: "none", borderRadius: 8 }}
              sandbox="allow-scripts allow-same-origin"
            />
          ) : null}
          {tab === "report" && report ? (
            <div className="tf-docs-md" style={{ maxHeight: "65vh", overflow: "auto", padding: "1rem" }}>
              <ReactMarkdown>{report}</ReactMarkdown>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
