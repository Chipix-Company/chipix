import React, { useEffect, useMemo, useRef, useState } from "react";
import Icon from "../icons";
import useOverlayClose from "../useOverlayClose";

function MetricTile({ label, value, sub, tone = "neutral" }) {
  return (
    <div className={`tf-pdash-tile ${tone}`}>
      <span className="tf-pdash-tile-lbl">{label}</span>
      <span className="tf-pdash-tile-val">{value}</span>
      {sub ? <span className="tf-pdash-tile-sub">{sub}</span> : null}
    </div>
  );
}

function formatTokens(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return "0";
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K`;
  return String(Math.round(n));
}

function PassRateRing({ passRate }) {
  if (passRate == null) return null;
  const r = 44;
  const c = 2 * Math.PI * r;
  const offset = c - (passRate / 100) * c;
  return (
    <div className="tf-pdash-ring" aria-hidden>
      <svg viewBox="0 0 100 100" width="100" height="100" className="tf-pdash-ring-svg">
        <circle cx="50" cy="50" r={r} fill="none" stroke="var(--tf-line)" strokeWidth="8" />
        <circle
          cx="50"
          cy="50"
          r={r}
          fill="none"
          stroke="var(--tf-good)"
          strokeWidth="8"
          strokeDasharray={c}
          strokeDashoffset={offset}
          strokeLinecap="round"
          transform="rotate(-90 50 50)"
        />
      </svg>
      <div className="tf-pdash-ring-center">
        <span className="tf-pdash-ring-val">{passRate}%</span>
        <span className="tf-pdash-ring-cap">pass rate</span>
      </div>
    </div>
  );
}

function RunOutcomeChart({ chartRuns = [] }) {
  const wrapRef = useRef(null);
  const [width, setWidth] = useState(800);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect?.width;
      if (w && w > 0) setWidth(Math.floor(w));
    });
    ro.observe(el);
    setWidth(el.clientWidth || 800);
    return () => ro.disconnect();
  }, []);

  if (!chartRuns.length) {
    return (
      <div className="tf-pdash-chart-panel tf-pdash-chart-empty">
        No verification runs yet — start one from the conversation.
      </div>
    );
  }

  const h = 112;
  const pad = { l: 12, r: 12, t: 12, b: 24 };
  const innerW = Math.max(100, width - pad.l - pad.r);
  const n = chartRuns.length;
  const gap = Math.max(3, Math.floor(innerW / n / 8));
  const barW = Math.max(8, Math.floor((innerW - gap * (n - 1)) / n));

  const colors = {
    pass: "var(--tf-good)",
    fail: "var(--tf-bad)",
    running: "var(--tf-busy)",
    other: "var(--tf-ink-4)",
  };

  return (
    <div ref={wrapRef} className="tf-pdash-chart-panel">
      <svg
        className="tf-pdash-chart"
        viewBox={`0 0 ${width} ${h}`}
        width="100%"
        height={h}
        preserveAspectRatio="none"
        role="img"
        aria-label="Recent run outcomes"
      >
        {chartRuns.map((r, i) => {
          const x = pad.l + i * (barW + gap);
          const fill = colors[r.outcome] || colors.other;
          const barH = r.outcome === "other" ? 16 : r.outcome === "running" ? 56 : 72;
          const y = h - pad.b - barH;
          return (
            <rect
              key={r.id || i}
              x={x}
              y={y}
              width={barW}
              height={barH}
              rx={4}
              fill={fill}
              opacity={r.outcome === "other" ? 0.45 : 0.95}
            />
          );
        })}
        <text x={pad.l} y={h - 6} className="tf-pdash-chart-cap" fill="var(--tf-ink-3)">
          oldest → newest ({chartRuns.length} runs)
        </text>
      </svg>
    </div>
  );
}

function TrendChart({ trendDays = [] }) {
  const max = Math.max(1, ...trendDays.map((d) => d.total));
  return (
    <div className="tf-pdash-chart-panel tf-pdash-trend" role="img" aria-label="Runs per day last 7 days">
      {trendDays.map((d) => {
        const passH = d.total ? Math.round((d.pass / max) * 72) : 0;
        const failH = d.total ? Math.round((d.fail / max) * 72) : 0;
        return (
          <div key={d.label} className="tf-pdash-trend-col" title={`${d.pass} passed, ${d.fail} failed`}>
            <div className="tf-pdash-trend-stack">
              {failH > 0 ? <span className="seg fail" style={{ height: failH }} /> : null}
              {passH > 0 ? <span className="seg pass" style={{ height: passH }} /> : null}
              {d.total === 0 ? <span className="seg empty" /> : null}
            </div>
            <span className="tf-pdash-trend-lbl">{d.label}</span>
            {d.total > 0 ? <span className="tf-pdash-trend-n">{d.total}</span> : null}
          </div>
        );
      })}
    </div>
  );
}

export default function DashboardOverlay({
  open,
  onClose,
  model,
  onSelectRun,
  onOpenIde,
  onStartVerification,
}) {
  const m = model || {};
  const files = m.files || {};
  const runs = m.runs || {};
  const tokenUsage = m.tokenUsage || {};
  const providerRows = Object.entries(tokenUsage.by_provider || {})
    .sort((a, b) => (b[1]?.total_tokens || 0) - (a[1]?.total_tokens || 0))
    .slice(0, 4);
  const modelRows = Object.entries(tokenUsage.by_model || {})
    .sort((a, b) => (b[1]?.total_tokens || 0) - (a[1]?.total_tokens || 0))
    .slice(0, 4);

  const headline = useMemo(() => {
    if (!runs.total) return "No verification runs yet";
    if (runs.inFlight > 0) {
      return `${runs.inFlight} in flight · ${runs.total} total runs`;
    }
    if (runs.passRate != null) {
      return `${runs.passed + runs.failed} finished runs on this project`;
    }
    return `${runs.total} verification run${runs.total === 1 ? "" : "s"}`;
  }, [runs]);

  const { closeButtonProps } = useOverlayClose({ open, onClose, closeOnEsc: false, label: "Close dashboard" });

  if (!open) return null;

  return (
    <div
      className="tf-overlay tf-dashboard-overlay tf-pdash"
      role="dialog"
      aria-modal="true"
      aria-label="Project dashboard"
    >
      <header className="tf-overlay-head tf-pdash-head">
        <div className="tf-overlay-title">
          <Icon.Activity width="18" height="18" />
          <span>{m.projectName || "Project"} · dashboard</span>
        </div>
        <div className="tf-overlay-actions">
          <button {...closeButtonProps}>
            <Icon.Close width="14" height="14" />
          </button>
        </div>
      </header>

      <div className="tf-pdash-body">
        <div className="tf-pdash-hero">
          <div className="tf-pdash-hero-copy">
            <p className="tf-pdash-lead">{headline}</p>
            <p className="tf-pdash-hint">
              Per-run failures and fixes stay in the conversation. This is your project health at a glance.
            </p>
            <div className="tf-pdash-hero-pills">
              <span className="tf-pdash-pill"><strong>{files.rtl ?? 0}</strong> RTL</span>
              <span className="tf-pdash-pill"><strong>{files.generated ?? 0}</strong> generated</span>
              <span className="tf-pdash-pill"><strong>{runs.total ?? 0}</strong> runs</span>
            </div>
          </div>
          {runs.passRate != null ? (
            <PassRateRing passRate={runs.passRate} />
          ) : null}
        </div>

        <div className="tf-pdash-split">
          <section className="tf-pdash-panel">
            <div className="tf-pdash-panel-head">
              <h2 className="tf-pdash-section-title">Design artifacts</h2>
              {onOpenIde ? (
                <button type="button" className="tf-btn ghost sm" onClick={onOpenIde}>
                  Open in IDE
                </button>
              ) : null}
            </div>
            <div className="tf-pdash-grid tf-pdash-grid-4">
              <MetricTile label="RTL sources" value={files.rtl ?? 0} sub=".sv / .v" />
              <MetricTile label="Specifications" value={files.spec ?? 0} sub="Spec and docs" />
              <MetricTile label="Generated" value={files.generated ?? 0} sub="TB, outputs" />
              <MetricTile label="All files" value={files.total ?? 0} sub="In project" />
            </div>
          </section>

          <section className="tf-pdash-panel">
            <h2 className="tf-pdash-section-title">Verification runs</h2>
            <div className="tf-pdash-grid tf-pdash-grid-4">
              <MetricTile label="Total" value={runs.total ?? 0} sub="All time" />
              <MetricTile
                label="Passed"
                value={runs.passed ?? 0}
                sub={runs.passRate != null ? `${runs.passRate}%` : "OK"}
                tone="good"
              />
              <MetricTile label="Failed" value={runs.failed ?? 0} sub="Attention" tone="bad" />
              <MetricTile
                label="In flight"
                value={runs.inFlight ?? 0}
                sub={runs.inFlight ? "Live in thread" : "Idle"}
                tone={runs.inFlight ? "busy" : "neutral"}
              />
            </div>
          </section>
        </div>

        <section className="tf-pdash-panel">
          <h2 className="tf-pdash-section-title">Token consumption</h2>
          <div className="tf-pdash-grid tf-pdash-grid-4">
            <MetricTile
              label="Total tokens"
              value={formatTokens(tokenUsage.total_tokens)}
              sub={`${tokenUsage.entries || 0} chat turns`}
              tone={tokenUsage.total_tokens ? "busy" : "neutral"}
            />
            <MetricTile
              label="Input"
              value={formatTokens(tokenUsage.input_tokens)}
              sub="Prompt + context"
            />
            <MetricTile
              label="Output"
              value={formatTokens(tokenUsage.output_tokens)}
              sub="Assistant response"
            />
            <MetricTile
              label="Estimated"
              value={tokenUsage.estimated_entries || 0}
              sub="Fallback counts"
            />
          </div>
          <div className="tf-pdash-token-breakdown">
            <div>
              <h3>Providers</h3>
              {providerRows.length ? providerRows.map(([name, row]) => (
                <span key={name}>
                  <strong>{name}</strong>
                  {formatTokens(row.total_tokens)} tokens
                </span>
              )) : <span>No token data yet</span>}
            </div>
            <div>
              <h3>Models</h3>
              {modelRows.length ? modelRows.map(([name, row]) => (
                <span key={name}>
                  <strong>{name}</strong>
                  {formatTokens(row.total_tokens)} tokens
                </span>
              )) : <span>No model data yet</span>}
            </div>
          </div>
        </section>

        <div className="tf-pdash-charts-row">
          <section className="tf-pdash-panel tf-pdash-panel-chart">
            <h2 className="tf-pdash-section-title">Run outcomes</h2>
            <RunOutcomeChart chartRuns={m.chartRuns || []} />
            <div className="tf-pdash-legend">
              <span><i className="dot pass" /> Passed</span>
              <span><i className="dot fail" /> Failed</span>
              <span><i className="dot busy" /> Running</span>
            </div>
          </section>

          <section className="tf-pdash-panel tf-pdash-panel-chart">
            <h2 className="tf-pdash-section-title">Activity · last 7 days</h2>
            <TrendChart trendDays={m.trendDays || []} />
          </section>
        </div>

        {(m.recentRuns || []).length > 0 ? (
          <section className="tf-pdash-panel tf-pdash-panel-runs">
            <h2 className="tf-pdash-section-title">Recent runs</h2>
            <ul className="tf-pdash-runs">
              {(m.recentRuns || []).map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    className={`tf-pdash-run-row ${r.tone} ${r.isActive ? "active" : ""}`}
                    onClick={() => onSelectRun?.(r.id)}
                  >
                    <span className={`tf-pdash-run-dot ${r.tone}`} />
                    <span className="tf-pdash-run-title">{r.title}</span>
                    <span className="tf-pdash-run-status">{r.status}</span>
                    {r.at ? (
                      <span className="tf-pdash-run-time">
                        {new Date(r.at).toLocaleString(undefined, {
                          month: "short",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {m.org?.totalProjects != null ? (
          <footer className="tf-pdash-org">
            Workspace · {m.org.totalProjects} projects · {m.org.totalRunsOrg ?? "—"} runs
            {m.org.passRateOrg != null ? ` · ${m.org.passRateOrg}% org pass rate` : ""}
          </footer>
        ) : null}
      </div>

      {onStartVerification ? (
        <footer className="tf-pdash-foot">
          <button type="button" className="tf-btn primary" onClick={onStartVerification}>
            Start verification
          </button>
        </footer>
      ) : null}
    </div>
  );
}
