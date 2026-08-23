import React, { useEffect, useMemo, useRef, useState } from "react";
import Icon from "../icons";
import TfMarkdown from "../TfMarkdown";
import FlowIndicator from "./FlowIndicator";
import { playChime, playThud } from "../useChipixSound";

function formatTime(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  } catch {
    return "";
  }
}

function formatTokens(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return "0";
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K`;
  return String(Math.round(n));
}

function fileGlyph(name = "") {
  const lower = name.toLowerCase();
  if (lower.endsWith(".sv") || lower.endsWith(".svh")) return "SV";
  if (lower.endsWith(".v") || lower.endsWith(".vh")) return "V";
  if (lower.endsWith(".md")) return "MD";
  if (lower.endsWith(".json")) return "JS";
  if (lower.endsWith(".py")) return "PY";
  const ext = lower.split(".").pop() || "";
  return ext.slice(0, 2).toUpperCase() || "??";
}

/* ============ Turn meta (who · when) ============ */
export function TurnMeta({ who, ts, tokenUsage }) {
  const [open, setOpen] = useState(false);
  const usage = tokenUsage || null;
  const totalTokens = Number(usage?.total_tokens ?? usage?.totalTokens ?? 0);
  return (
    <div className="tf-turn-meta">
      {who ? <span className="who">{who}</span> : null}
      {ts ? <span>·</span> : null}
      {ts ? <span className="when">{formatTime(ts)}</span> : null}
      {totalTokens > 0 ? (
        <span className="tf-token-wrap">
          <button
            type="button"
            className="tf-token-dot"
            onClick={() => setOpen((v) => !v)}
            aria-label="Show token usage"
            title={`${formatTokens(totalTokens)} tokens`}
          >
            T
          </button>
          {open ? (
            <span className="tf-token-popover" role="status">
              <strong>{formatTokens(totalTokens)} tokens</strong>
              <span>Input: {formatTokens(usage.input_tokens ?? usage.inputTokens)}</span>
              <span>Output: {formatTokens(usage.output_tokens ?? usage.outputTokens)}</span>
              {usage.provider || usage.model ? (
                <span>{[usage.provider, usage.model].filter(Boolean).join(" · ")}</span>
              ) : null}
              {usage.is_estimated || usage.isEstimated ? <em>Estimated</em> : <em>Provider reported</em>}
            </span>
          ) : null}
        </span>
      ) : null}
    </div>
  );
}

/* ============ User bubble ============ */
export function UserBubble({ text, ts, tokenUsage, onEdit, editLabel = "Edit" }) {
  return (
    <div className="tf-you-wrap">
      <div className="tf-you-meta-row">
        <TurnMeta who="You" ts={ts} tokenUsage={tokenUsage} />
        {onEdit ? (
          <button
            type="button"
            className="tf-you-edit"
            onClick={onEdit}
            aria-label={editLabel}
          >
            {editLabel}
          </button>
        ) : null}
      </div>
      <div className="tf-you">{text}</div>
    </div>
  );
}

/* ============ Said (AI prose, markdown-aware) ============ */
export function Said({ text, ts, tokenUsage, streaming = false }) {
  return (
    <div className="tf-system">
      <TurnMeta who="Chipix" ts={ts} tokenUsage={tokenUsage} />
      <div className={`tf-said${streaming ? " streaming" : ""}`}>
        <TfMarkdown>{text}</TfMarkdown>
        {streaming ? <span className="tf-caret" aria-hidden /> : null}
      </div>
    </div>
  );
}

/* ============ Thinking spinner ============ */
export function Thinking({ text = "Thinking" }) {
  return (
    <div className="tf-thinking">
      <span className="dots"><span /><span /><span /></span>
      <span>{text}</span>
    </div>
  );
}

/* ============ Section break ============ */
export function SectionBreak({ label }) {
  return <div className="tf-section-break"><span>{label}</span></div>;
}

/* ============ Ask card ============ */
export function AskCard({ question, sub, choices = [], chosenId, locked, onChoose }) {
  return (
    <div className="tf-card">
      <div className="tf-ask">
        <div className="tf-ask-q">
          <TfMarkdown>{question}</TfMarkdown>
        </div>
        {sub ? (
          <div className="tf-ask-sub">
            <TfMarkdown>{sub}</TfMarkdown>
          </div>
        ) : null}
        <div className="tf-ask-choices">
          {choices.map((c) => {
            const isPicked = chosenId === c.id;
            const cls = ["tf-choice", isPicked ? "chosen" : "", locked && !isPicked ? "locked" : ""]
              .filter(Boolean).join(" ");
            return (
              <button
                type="button"
                key={c.id}
                className={cls}
                onClick={() => !locked && onChoose?.(c.id)}
                disabled={locked && !isPicked}
              >
                {c.label}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ============ Plan card (mock: .card.plan + .plan-h + .plan-step) ============ */
export function PlanCard({
  title = "Plan",
  sub,
  summary,
  phase,
  assumptions = [],
  constraints = [],
  risks = [],
  steps = [],
  actions,
  superseded = false,
  refineLoading = false,
  runCadenceSimulation = false,
  cadenceConnected = false,
  onPlanAction,
}) {
  const [refineOpen, setRefineOpen] = useState(false);
  const [refineText, setRefineText] = useState("");
  const phaseLabel = phase === "verification" ? "Verification" : phase === "design" ? "Design" : null;
  const planHead =
    title && title !== "Plan" ? `${title} · ${steps.length} steps` : `${steps.length} steps`;
  const locked = superseded;
  const submitRefine = () => {
    const t = refineText.trim();
    if (!t) return;
    onPlanAction?.("refine", t);
    setRefineText("");
    setRefineOpen(false);
  };
  return (
    <div
      className={`tf-card tf-plan tf-verify-card${superseded ? " tf-superseded" : ""}`}
      data-superseded={superseded ? "1" : undefined}
    >
      <FlowIndicator phase="plan" />
      <div className="tf-plan-h">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M9 11l3 3L22 4" />
          <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
        </svg>
        <span>{planHead}</span>
        {phaseLabel ? <span className="tf-plan-tag">{phaseLabel}</span> : null}
        {superseded ? <span className="tf-plan-tag">Superseded</span> : null}
      </div>
      {sub ? <div className="tf-plan-sub">{sub}</div> : null}
      {summary ? (
        <section className="tf-plan-understood" aria-label="What I understood">
          <div className="tf-plan-understood-label">What I understood</div>
          <TfMarkdown className="tf-plan-understood-md">{summary}</TfMarkdown>
        </section>
      ) : null}
      {assumptions?.length > 0 ? (
        <section className="tf-plan-meta-section" aria-label="Assumptions">
          <div className="tf-plan-meta-label">Assumptions</div>
          <ul className="tf-plan-meta-list">
            {assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {constraints?.length > 0 ? (
        <section className="tf-plan-meta-section" aria-label="Constraints">
          <div className="tf-plan-meta-label">Planning rules</div>
          <ul className="tf-plan-meta-list tf-plan-constraints">
            {constraints.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {risks?.length > 0 ? (
        <section className="tf-plan-meta-section" aria-label="Risks and open items">
          <div className="tf-plan-meta-label">Risks and open items</div>
          <ul className="tf-plan-meta-list tf-plan-risks">
            {risks.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {steps.length > 0 ? (
        <div className="tf-plan-steps-label">What I will implement</div>
      ) : null}
      {steps.map((s, i) => (
        <div key={s.id ?? `step-${i}`} className={`tf-plan-step ${s.state || "todo"}`}>
          <span className="mark">
            {s.state === "done" ? "✓" : s.state === "now" ? "•" : ""}
          </span>
          <div>
            <div className="label">{s.label}</div>
            {s.why ? <div className="why">{s.why}</div> : null}
          </div>
        </div>
      ))}

      {refineOpen && !locked ? (
        <div className="tf-plan-refine">
          <textarea
            className="tf-plan-refine-input"
            placeholder="What should change? e.g. add a stress test for FIFO overflow."
            value={refineText}
            onChange={(e) => setRefineText(e.target.value)}
            disabled={refineLoading}
            rows={3}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submitRefine();
            }}
          />
          <div className="tf-plan-refine-actions">
            <button
              type="button"
              className="tf-btn"
              onClick={() => { setRefineOpen(false); setRefineText(""); }}
              disabled={refineLoading}
            >
              Cancel
            </button>
            <button
              type="button"
              className="tf-btn primary"
              onClick={submitRefine}
              disabled={refineLoading || !refineText.trim()}
            >
              {refineLoading ? "Refining…" : "Send refinement"}
            </button>
          </div>
        </div>
      ) : null}

      {cadenceConnected && !locked ? (
        <label className="tf-plan-cadence-opt">
          <input
            type="checkbox"
            checked={Boolean(runCadenceSimulation)}
            onChange={(e) => onPlanAction?.("toggle-run-cadence", e.target.checked)}
          />
          <span>Run full Cadence simulation after generation</span>
        </label>
      ) : null}

      {actions?.length ? (
        <div className="tf-card-actions">
          {actions.map((a) => {
            const isRefine = a.id === "refine";
            const handleClick = () => {
              if (locked) return;
              if (isRefine) {
                setRefineOpen((v) => !v);
              } else {
                onPlanAction?.(a.id);
              }
            };
            return (
              <button
                type="button"
                key={a.id}
                className={`tf-btn ${a.kind === "primary" || a.id === "approve" ? "primary" : a.kind === "danger" ? "danger" : ""}`}
                onClick={handleClick}
                disabled={locked || (isRefine && refineLoading)}
              >
                {a.label}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

/* ============ Staged: Prepare card ============ */
export function StagedPrepareCard({
  title = "Plan a verification strategy",
  summary,
  ready = false,
  loading = false,
  error,
  onRecommend,
}) {
  return (
    <div className="tf-card tf-staged-prep tf-verify-card">
      <FlowIndicator phase="strategy" />
      <div className="tf-card-h">
        <div className="icon" style={{ background: "var(--tf-info-bg)", color: "var(--tf-info)" }}>
          <Icon.Brain />
        </div>
        <div>
          <div className="title">{title}</div>
          <div className="sub">Prepare → Recommend → Plan → Approve → Run</div>
        </div>
      </div>
      <div className="tf-staged-body">
        <p className="tf-staged-prose">
          I scanned your spec + RTL. Before I pick a strategy, I'll show you how I understand the design — every test I write will reinforce that model, so this is the most important checkpoint.
        </p>
        {summary ? <p className="tf-staged-prose tf-staged-summary">{summary}</p> : null}
        {error ? <p className="tf-staged-error">{error}</p> : null}
      </div>
      <div className="tf-card-actions">
        <button
          type="button"
          className="tf-btn primary"
          onClick={() => onRecommend?.()}
          disabled={loading || (!ready && !error)}
        >
          {loading ? "Scanning your design…" : ready ? "Review my understanding" : error ? "Retry preparation" : "Scanning…"}
        </button>
      </div>
    </div>
  );
}

/* ============ Staged: Recommend card (strategy chips) ============ */

function stagedStrategyIcon(strategyId) {
  switch (strategyId) {
    case "unitsim":
      return Icon.StratSim;
    case "formal":
      return Icon.StratFormal;
    case "uvm":
      return Icon.StratUvm;
    case "all":
      return Icon.StratAll;
    default:
      return Icon.Beaker;
  }
}

export function StagedRecommendCard({
  title = "Pick a verification strategy",
  sub,
  complexity,
  strategies = [],
  picked,
  loading = false,
  onPickStrategy,
}) {
  return (
    <div className="tf-card tf-staged-rec tf-verify-card">
      <FlowIndicator phase="strategy" />
      <div className="tf-card-h">
        <div className="icon" style={{ background: "var(--tf-info-bg)", color: "var(--tf-info)" }}>
          <Icon.Beaker />
        </div>
        <div>
          <div className="title">{title}</div>
          <div className="sub">
            {sub || (complexity ? `Design complexity: ${complexity}` : "Click a chip to draft a plan you can review before we run it.")}
          </div>
        </div>
      </div>
      <div className="tf-staged-rec-grid">
        {strategies.length === 0 ? (
          <div className="tf-staged-empty">No recommendations were returned.</div>
        ) : null}
        {strategies.map((s) => {
          const isPicked = picked === s.id;
          const isLoading = loading && isPicked;
          const Ico = stagedStrategyIcon(s.id);
          const conf = s.confidence ? String(s.confidence).toLowerCase().trim() : "";
          const confClass = ["high", "medium", "low"].includes(conf) ? conf : "";
          return (
            <button
              type="button"
              key={s.id}
              className={`tf-staged-chip${s.recommended ? " reco" : ""}${isPicked ? " picked" : ""}`}
              onClick={() => !loading && onPickStrategy?.(s.id)}
              disabled={loading}
            >
              <span className="chip-icon-wrap" aria-hidden>
                <Ico width="22" height="22" strokeWidth={2} />
              </span>
              <div className="chip-text">
                <div className="chip-head">
                  <span className="lbl">{s.label}</span>
                  {s.recommended ? <span className="tag reco">Recommended</span> : null}
                  {s.confidence ? (
                    <span className={`tag conf${confClass ? ` ${confClass}` : ""}`}>
                      {s.confidence}
                    </span>
                  ) : null}
                </div>
                {s.rationale ? <div className="rationale">{s.rationale}</div> : null}
                {isLoading ? <div className="loading">Drafting plan…</div> : null}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ============ File card ============ */
export function FileCard({ name, summary, addedLines, language, onOpen }) {
  return (
    <button type="button" className="tf-card tf-file" onClick={() => onOpen?.()}>
      <div className="glyph">{fileGlyph(name)}</div>
      <div className="info">
        <div className="name">{name}</div>
        <div className="what">
          {summary ? <span>{summary}</span> : null}
          {addedLines ? <span className="add">  +{addedLines}</span> : null}
          {!summary && !addedLines && language ? <span>{language}</span> : null}
        </div>
      </div>
      <Icon.ChevronRight className="arrow" width={16} height={16} />
    </button>
  );
}

/**
 * Group a unified diff into split rows for side-by-side rendering.
 *
 * Walks through the line stream pairing consecutive `del` runs with the
 * `add` runs that immediately follow — that's the LCS-equivalent for the
 * simple case where the backend already gave us aligned hunks. Context
 * lines appear on both sides at the same row. Pure-add chunks have empty
 * left cells; pure-del chunks have empty right cells.
 */
function buildSplitRows(lines = []) {
  const rows = [];
  let i = 0;
  while (i < lines.length) {
    const ln = lines[i];
    if (!ln) { i += 1; continue; }
    if (ln.kind === "ctx" || !ln.kind) {
      rows.push({
        left: { ln: ln.ln ?? "", code: ln.code, kind: "ctx" },
        right: { ln: ln.ln ?? "", code: ln.code, kind: "ctx" },
      });
      i += 1;
      continue;
    }
    // Collect a run of dels followed by a run of adds — pair them positionally.
    const dels = [];
    const adds = [];
    while (i < lines.length && lines[i]?.kind === "del") { dels.push(lines[i]); i += 1; }
    while (i < lines.length && lines[i]?.kind === "add") { adds.push(lines[i]); i += 1; }
    const max = Math.max(dels.length, adds.length);
    for (let k = 0; k < max; k += 1) {
      rows.push({
        left: dels[k] ? { ln: dels[k].ln ?? "", code: dels[k].code, kind: "del" } : null,
        right: adds[k] ? { ln: adds[k].ln ?? "", code: adds[k].code, kind: "add" } : null,
      });
    }
  }
  return rows;
}

/* ============ Diff card ============ */
export function DiffCard({ filename, summary, lines = [], status = "pending", why, onApply, onReject, onEdit, streaming = false }) {
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [view, setView] = useState("unified");
  const splitRows = useMemo(() => (view === "split" ? buildSplitRows(lines) : []), [view, lines]);
  const submitReject = (skipReason = false) => {
    const r = skipReason ? "" : reason.trim();
    setRejectOpen(false);
    setReason("");
    onReject?.(r || undefined);
  };
  return (
    <div className="tf-card tf-diff">
      <div className="tf-card-h tf-diff-h">
        <div className="icon"><Icon.Edit /></div>
        <div>
          <div className="title">{filename}</div>
          {why ? <div className="sub">{why}</div> : null}
        </div>
        <span className="summary">
          {summary}
          {streaming ? <span className="tf-diff-streaming" aria-hidden><span /><span /><span /></span> : null}
        </span>
        <button
          type="button"
          className="tf-diff-viewtoggle"
          onClick={() => setView((v) => (v === "unified" ? "split" : "unified"))}
          title={view === "unified" ? "Show side-by-side" : "Show unified"}
        >
          {view === "unified" ? "Side-by-side" : "Unified"}
        </button>
      </div>
      {view === "split" ? (
        <div className="tf-diff-body split">
          {splitRows.map((row, i) => (
            <div key={i} className="tf-diff-srow">
              <div className={`tf-diff-sside left ${row.left ? row.left.kind : "empty"}`}>
                <span className="ln">{row.left ? row.left.ln : ""}</span>
                <span className="code">{row.left ? row.left.code : " "}</span>
              </div>
              <div className={`tf-diff-sside right ${row.right ? row.right.kind : "empty"}`}>
                <span className="ln">{row.right ? row.right.ln : ""}</span>
                <span className="code">{row.right ? row.right.code : " "}</span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="tf-diff-body">
          {lines.map((line, i) => {
            const cls = `tf-diff-line ${line.kind === "add" ? "add" : line.kind === "del" ? "del" : ""}`;
            const marker = line.kind === "add" ? "+" : line.kind === "del" ? "−" : " ";
            return (
              <div key={i} className={cls}>
                <span className="ln">{line.ln ?? ""}</span>
                <span className="marker">{marker}</span>
                <span className="code">{line.code}</span>
              </div>
            );
          })}
        </div>
      )}

      {rejectOpen && status === "pending" ? (
        <div className="tf-diff-reject">
          <label className="tf-diff-reject-lbl">
            Why are you rejecting this? <span className="opt">(optional, helps the next try)</span>
          </label>
          <textarea
            className="tf-diff-reject-input"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. swap doesn't address parity inversion — the actual bug is in the framing logic"
            rows={2}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submitReject(false);
              if (e.key === "Escape") { setRejectOpen(false); setReason(""); }
            }}
          />
          <div className="tf-diff-reject-actions">
            <button type="button" className="tf-btn sm" onClick={() => { setRejectOpen(false); setReason(""); }}>
              Cancel
            </button>
            <button type="button" className="tf-btn sm" onClick={() => submitReject(true)}>
              Reject without reason
            </button>
            <button type="button" className="tf-btn sm primary" onClick={() => submitReject(false)} disabled={!reason.trim()}>
              Send rejection
            </button>
          </div>
        </div>
      ) : null}

      <div className="tf-card-actions" data-applied={status === "applied" ? "1" : undefined}>
        <button type="button" className="tf-btn primary" onClick={() => onApply?.()} disabled={status !== "pending"}>
          <Icon.Check width="14" height="14" /> Apply change
        </button>
        <button type="button" className="tf-btn" onClick={() => onEdit?.()} disabled={status !== "pending"}>Open in editor</button>
        <span className="spacer" />
        <button
          type="button"
          className="tf-btn danger"
          onClick={() => setRejectOpen((v) => !v)}
          disabled={status !== "pending"}
        >
          {rejectOpen ? "Hide reject" : "Reject"}
        </button>
      </div>
    </div>
  );
}

/* ============ Run card ============ */
function RunMetricsStrip({ kpis, strategies = [] }) {
  const total = kpis?.total ?? 0;
  const passed = kpis?.passed ?? 0;
  const failed = kpis?.failed ?? 0;
  const cov = kpis?.coverage_pct ?? kpis?.coverage ?? null;
  const hasKpi = total > 0 || passed > 0 || failed > 0;
  const visibleStrategies = strategies.filter(
    (s) => String(s.status || "idle").toLowerCase() !== "idle"
      || Number(s.total) > 0
      || Number(s.passed) > 0
      || Number(s.failed) > 0,
  );
  if (!hasKpi && !visibleStrategies.length) return null;

  return (
    <div className="tf-run-metrics" aria-label="Verification metrics">
      {hasKpi ? (
        <span className="tf-run-metrics-kpi">
          {passed}<span className="dim">/{total}</span> scenarios
          {failed > 0 ? <span className="dim"> · {failed} failed</span> : null}
          {cov != null ? <span className="dim"> · {Math.round(Number(cov) <= 1 ? Number(cov) * 100 : Number(cov))}% cov</span> : null}
        </span>
      ) : null}
      {visibleStrategies.map((s) => {
        const status = String(s.status || "idle").toLowerCase();
        const counts = s.passed != null && s.total != null ? `${s.passed}/${s.total}` : s.detail || "";
        return (
          <span key={s.id || s.name} className={`tf-strat-chip ${status}`} title={s.detail || s.name}>
            <span className="dot" />
            <span className="name">{s.name}</span>
            {counts ? <span className="counts">{counts}</span> : null}
          </span>
        );
      })}
    </div>
  );
}

export function RunCard({
  runId,
  title,
  status = "running",
  stages = [],
  currentPhase = null,
  phaseCount = null,
  elapsed,
  log = [],
  runKpis = null,
  strategies = [],
  downloadArtifactIds = [],
  generatedArtifactIds = [],
  downloadFilename = "",
  onCancel,
  onDownload,
  onUploadLog,
}) {
  const [logOpen, setLogOpen] = useState(false);
  const statusClass = status === "completed" || status === "passed"
    ? "good"
    : status === "failed"
      || status === "partial"
      || status === "cancelled"
      || status === "interrupted"
      || status === "validation_failed"
      || status === "compile_gate_failed"
      || status === "post_process_failed"
      || status === "stream_incomplete"
    ? "bad"
    : status === "queued" || status === "idle"
    ? "idle"
    : "busy";
  const statusLabel = String(status || "running").toUpperCase();
  const currentStage = stages.find((s) => s.state === "now");
  // Prefer the finer 10-phase narrative when available; fall back to stage.why.
  const phaseDisplay = currentPhase && currentPhase.label
    ? {
        label: currentPhase.label,
        sub: phaseCount ? `Phase ${currentPhase.idx + 1} of ${phaseCount}` : null,
        idx: currentPhase.idx,
      }
    : currentStage
      ? { label: currentStage.label, sub: currentStage.why || null, idx: null }
      : null;

  return (
    <div className="tf-card tf-verify-card">
      <FlowIndicator phase="run" />
      <div className="tf-run">
        <div className="tf-run-top">
          <span className={`tf-run-status ${statusClass}`}>
            {statusClass === "busy" ? <span className="live" /> : null}
            {statusLabel}
          </span>
          <div className="tf-run-title">{title || `Verification · ${String(runId || "").slice(0, 8)}`}</div>
          {elapsed ? <span className="tf-run-elapsed">{elapsed}</span> : null}
        </div>

        {phaseDisplay && statusClass === "busy" ? (
          <div className="tf-run-now">
            <span className="icon">⟳</span>
            <div>
              <strong>{phaseDisplay.label}</strong>
              {phaseDisplay.sub ? <div className="tf-run-now-sub">{phaseDisplay.sub}</div> : null}
            </div>
          </div>
        ) : null}

        {phaseCount && currentPhase && statusClass === "busy" ? (
          <div className="tf-run-phaseprog" aria-label={`Phase ${currentPhase.idx + 1} of ${phaseCount}`}>
            {Array.from({ length: phaseCount }).map((_, i) => (
              <span
                key={i}
                className={`tf-run-phaseprog-cell ${i < currentPhase.idx ? "done" : i === currentPhase.idx ? "now" : "todo"}`}
              />
            ))}
          </div>
        ) : null}

        <RunMetricsStrip kpis={runKpis} strategies={strategies} />

        {stages.length ? (
          <div className="tf-run-stages">
            {stages.map((s) => (
              <span key={s.id} className={`tf-stage ${s.state || ""}`}>{s.label}</span>
            ))}
          </div>
        ) : null}

        <button type="button" className={`tf-run-log-toggle${logOpen ? " open" : ""}`} onClick={() => setLogOpen((v) => !v)}>
          <Icon.ChevronDown width={11} height={11} />
          {logOpen ? "Hide technical log" : "Show technical log"}
        </button>
        <div className={`tf-run-log${logOpen ? " open" : ""}`}>
          {!logOpen ? null : log.length === 0 ? (
            <span className="tf-run-log-empty">No events yet.</span>
          ) : (
            log.map((entry, i) => (
              <div key={i} className={entry.level === "error" ? "err" : entry.level === "warn" ? "warn" : ""}>
                <span className="ts">{formatTime(entry.ts)}</span>
                {entry.message}
              </div>
            ))
          )}
        </div>

        {(onCancel || onDownload || onUploadLog) ? (
          <div className="tf-run-extras">
            {statusClass === "busy" && onCancel ? (
              <button type="button" className="tf-btn danger sm" onClick={() => onCancel(runId)}>Cancel</button>
            ) : null}
            {(statusClass === "good" || statusClass === "bad") && onUploadLog ? (
              <button
                type="button"
                className="tf-btn sm"
                onClick={() => onUploadLog({
                  runId,
                  generatedArtifactIds: generatedArtifactIds.length ? generatedArtifactIds : downloadArtifactIds,
                })}
              >
                <Icon.Upload width="12" height="12" /> Upload simulator log
              </button>
            ) : null}
            {(statusClass === "good" || statusClass === "bad") && onDownload ? (
              <button
                type="button"
                className="tf-btn sm"
                onClick={() => onDownload(runId, {
                  artifactIds: downloadArtifactIds,
                  filename: downloadFilename,
                })}
              >
                <Icon.Download width="12" height="12" /> Download
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/* ============ Cadence Xcelium live tool-call card ============ */
function cadenceTone(status) {
  const value = String(status || "detecting").toLowerCase();
  if (value === "passed") return "good";
  if (value === "failed" || value === "error" || value === "unavailable" || value === "needs_action") return "bad";
  if (value === "skipped" || value === "needs_review") return "neutral";
  return "busy";
}

function cadencePhaseMark(state) {
  if (state === "done") return <Icon.Check width="12" height="12" />;
  if (state === "bad") return <Icon.X width="12" height="12" />;
  if (state === "skipped") return "—";
  return <span className="tf-cadence-run-pulse" />;
}

export function CadenceRunCard({
  status = "detecting",
  detection = null,
  phases = [],
  events = [],
  runId = null,
  reason = "",
  summary = "",
  regression = null,
  coverage = null,
  traceability = null,
  findings = [],
  report = null,
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const tone = cadenceTone(status);
  const statusLabel = String(status || "detecting").replace(/_/g, " ");
  const activePhase = phases.find((phase) => phase.state === "now");
  const activeCommand = activePhase?.command || "";
  const latestEvent = events[events.length - 1] || null;
  const detectionStatus = String(detection?.status || "detecting").replace(/_/g, " ");
  const subtitle = reason
    || summary
    || latestEvent?.message
    || (detection?.run_ready ? "xrun and the Cadence license are ready." : "Checking xrun, setup script, and license readiness.");

  return (
    <div className={`tf-card tf-cadence-run tf-verify-card tone-${tone}`}>
      <FlowIndicator phase="run" />
      <div className="tf-cadence-run-head">
        <span className={`tf-cadence-run-icon tone-${tone}`}><Icon.Cpu width="17" height="17" /></span>
        <div className="tf-cadence-run-heading">
          <div className="tf-cadence-run-eyebrow">EDA tool call</div>
          <div className="tf-cadence-run-title">Cadence Xcelium</div>
        </div>
        <span className={`tf-cadence-run-status tone-${tone}`}>
          {tone === "busy" ? <span className="live" /> : null}
          {statusLabel}
        </span>
      </div>

      <p className="tf-cadence-run-sub">{subtitle}</p>

      <div className="tf-cadence-run-detection">
        <span><b>Detection</b>{detectionStatus}</span>
        {detection?.version ? <span><b>Version</b>{detection.version}</span> : null}
        <span><b>License</b>{detection?.license_ready ? "ready" : "not ready"}</span>
        {runId ? <span><b>Run</b>{String(runId).slice(0, 16)}</span> : null}
      </div>

      {(regression || coverage || traceability || findings.length) ? (
        <div className="tf-cadence-run-detection tf-cadence-results">
          {regression ? <span><b>Tests</b>{regression.passed || 0} pass · {regression.failed || 0} fail</span> : null}
          {coverage?.functional ? <span><b>Functional</b>{coverage.functional.achieved == null ? "missing" : `${coverage.functional.achieved}%`} / {coverage.functional.target}%</span> : null}
          {coverage?.code ? <span><b>Code</b>{coverage.code.achieved == null ? "missing" : `${coverage.code.achieved}%`} / {coverage.code.target}%</span> : null}
          {traceability ? <span><b>Requirements</b>{traceability.requirements_covered || 0}/{traceability.requirements_total || 0}</span> : null}
          {findings.length ? <span><b>Findings</b>{findings.length}</span> : null}
          {report?.filename ? <span><b>Report</b>{report.filename}</span> : null}
        </div>
      ) : null}

      <div className="tf-cadence-run-phases" role="list" aria-label="Cadence execution phases">
        {phases.map((phase) => (
          <div key={phase.id} className={`tf-cadence-run-phase ${phase.state || "todo"}`} role="listitem">
            <span className="mark">{cadencePhaseMark(phase.state)}</span>
            <span className="name">{phase.label}</span>
            {phase.detail ? <span className="detail">{phase.detail}</span> : null}
            {phase.returncode != null ? <span className="rc">rc {phase.returncode}</span> : null}
          </div>
        ))}
      </div>

      {activeCommand ? (
        <div className="tf-cadence-run-command">
          <span>Current command</span>
          <code>{activeCommand}</code>
        </div>
      ) : null}

      {events.length ? (
        <>
          <button
            type="button"
            className={`tf-cadence-run-toggle${detailsOpen ? " open" : ""}`}
            onClick={() => setDetailsOpen((value) => !value)}
          >
            <Icon.ChevronDown width="11" height="11" />
            {detailsOpen ? "Hide Cadence timeline" : "Show Cadence timeline"}
          </button>
          {detailsOpen ? (
            <div className="tf-cadence-run-events">
              {events.map((event, index) => (
                <div key={`${event.ts || "event"}-${index}`} className={event.level === "error" ? "bad" : ""}>
                  <span className="ts">{formatTime(event.ts)}</span>
                  <span>{event.message}</span>
                  {event.command ? <code>{event.command}</code> : null}
                </div>
              ))}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

/* ============ Spec-grounded design bug / closure report ============ */
function coverageDomainLabel(domain = {}) {
  if (domain.achieved == null) return `Missing · target ${domain.target ?? "—"}%`;
  return `${domain.achieved}% / ${domain.target}% · ${domain.closed ? "closed" : "open"}`;
}

export function DesignBugReportCard({
  status = "needs_review",
  report = {},
  artifactId = null,
  counts = {},
  coverage = {},
  traceability = {},
  findings = [],
  integrity = {},
  onOpenReport,
  onOpenSpec,
  onOpenRtl,
  onDownloadEvidence,
}) {
  const tone = cadenceTone(status);
  const topFindings = findings.slice(0, 3);
  const firstSpecRef = topFindings.find((finding) => finding.spec_ref?.artifact_id)?.spec_ref || null;
  const firstRtlRef = topFindings.flatMap((finding) => finding.rtl_refs || []).find((ref) => ref?.artifact_id) || null;
  return (
    <div className={`tf-card tf-design-bug-report tf-verify-card tone-${tone}`}>
      <FlowIndicator phase="verdict" />
      <div className="tf-design-bug-head">
        <span className={`tf-cadence-run-icon tone-${tone}`}><Icon.Activity width="17" height="17" /></span>
        <div>
          <div className="tf-cadence-run-eyebrow">Spec-grounded verification closure</div>
          <div className="tf-cadence-run-title">{report.title || "Design Verification Bug Report"}</div>
        </div>
        <span className={`tf-cadence-run-status tone-${tone}`}>{String(status).replace(/_/g, " ")}</span>
      </div>

      <div className="tf-design-bug-counts">
        <span className="confirmed"><b>{counts.Confirmed || 0}</b>Confirmed</span>
        <span className="likely"><b>{counts.Likely || 0}</b>Likely</span>
        <span className="inconclusive"><b>{counts.Inconclusive || 0}</b>Inconclusive</span>
        <span><b>{traceability.coverage_percentage || 0}%</b>Spec traced</span>
      </div>

      <div className="tf-design-bug-coverage">
        <div><span>Functional coverage</span><b>{coverageDomainLabel(coverage.functional)}</b></div>
        <div><span>Code coverage</span><b>{coverageDomainLabel(coverage.code)}</b></div>
        <div><span>UVM integrity</span><b>{integrity.passed ? "Passed" : "Blocked"}</b></div>
      </div>

      {topFindings.length ? (
        <div className="tf-design-bug-findings">
          {topFindings.map((finding) => (
            <div key={finding.id} className={`finding tier-${String(finding.tier || "").toLowerCase()}`}>
              <span className="tier">{finding.tier}</span>
              <b>{finding.id} · {finding.requirement_id || "Unmapped requirement"}</b>
              <p>{finding.title || finding.observed}</p>
              {finding.spec_ref?.page ? <small>Spec page {finding.spec_ref.page}, line {finding.spec_ref.line || "—"}</small> : null}
            </div>
          ))}
        </div>
      ) : (
        <p className="tf-cadence-run-sub">No design-bug finding was eligible for confirmation. Review open coverage and traceability gaps before sign-off.</p>
      )}

      <div className="tf-card-actions">
        <button type="button" className="tf-btn primary sm" disabled={!artifactId} onClick={() => onOpenReport?.()}>
          Open professional Markdown report
        </button>
        {firstSpecRef ? <button type="button" className="tf-btn sm" onClick={() => onOpenSpec?.(firstSpecRef)}>Open spec reference</button> : null}
        {firstRtlRef ? <button type="button" className="tf-btn sm" onClick={() => onOpenRtl?.(firstRtlRef)}>Open RTL evidence</button> : null}
        {onDownloadEvidence ? <button type="button" className="tf-btn sm" onClick={() => onDownloadEvidence()}>Download evidence</button> : null}
        <span className="tf-design-bug-file">{report.filename || "Report artifact pending"}</span>
      </div>
    </div>
  );
}

function flattenVerdictActions(actions) {
  return (actions || []).flatMap((a) => (Array.isArray(a) ? a : [a])).filter(Boolean);
}

function factStatusTone(value = "") {
  const v = String(value).toLowerCase();
  if (/pass|success|complete|ok/.test(v)) return "good";
  if (/fail|error|validation/.test(v)) return "bad";
  if (/partial|warn|pending|skip/.test(v)) return "busy";
  return "neutral";
}

function formatFactValue(value = "") {
  return String(value).replace(/_/g, " ");
}

function verdictActionClass(action) {
  if (action.kind === "primary") return "primary sm";
  if (action.kind === "danger") return "danger sm";
  if (action.id === "rerun" || action.id === "promote" || action.id === "run-cadence") return "primary sm";
  if (action.id === "open-dashboard") return "ghost sm";
  return "ghost sm";
}

/* ============ Verdict card ============
   The climax of the verification flow. Mirrors the Mental Model
   NarrativeStrip pattern: tinted gradient, eyebrow, prominent headline,
   summary sentence. Plays a soft chime on success / a low thud on failure
   the first time the card mounts — felt acknowledgement that the system
   reached a conclusion.  */
export function VerdictCard({ ok, title, sub, facts = [], actions = [], onAction }) {
  const playedRef = useRef(false);
  useEffect(() => {
    if (playedRef.current) return;
    playedRef.current = true;
    if (ok) playChime();
    else playThud();
  }, [ok]);

  const eyebrow = ok ? "Verification complete" : "Verification failed";
  const headline = title || (ok ? "All checks passed." : "Something didn't pass.");
  const flatActions = flattenVerdictActions(actions);

  return (
    <div className={`tf-card tf-verdict tf-verify-card ${ok ? "good" : "bad"}`}>
      <FlowIndicator phase="verdict" />
      <div className="tf-verdict-inner">
      <div className="tf-verdict-narrative">
        <div className="tf-verdict-eyebrow">
          <span className="tf-verdict-icon" aria-hidden>
            {ok ? <Icon.Check width="16" height="16" /> : <Icon.X width="16" height="16" />}
          </span>
          <span className="tf-verdict-eyebrow-text">{eyebrow}</span>
        </div>
        <h3 className="tf-verdict-headline">{headline}</h3>
        {sub ? <p className="tf-verdict-summary">{sub}</p> : null}
      </div>
      {facts.length ? (
        <div className="tf-verdict-facts" role="list">
          {facts.map((f, i) => {
            const tone = factStatusTone(f.v);
            return (
              <div key={`${f.k}-${i}`} className={`tf-verdict-fact tone-${tone}`} role="listitem">
                <span className="k">{f.k}</span>
                <span className="v">{formatFactValue(f.v)}</span>
              </div>
            );
          })}
        </div>
      ) : null}
      </div>
      {flatActions.length ? (
        <div className="tf-card-actions">
          {flatActions.map((a) => (
            <button
              type="button"
              key={a.id}
              className={`tf-btn ${verdictActionClass(a)}`}
              onClick={() => onAction?.(a.id)}
            >
              {a.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/* ============ Why card ============ */
export function WhyCard({
  title = "Why it failed",
  verdict = "Failed",
  body,
  quote,
  file,
  line,
  source,
  openLink,
  onOpenLink,
  onProposeFix,
}) {
  // Back-compat: build code excerpt from explicit { file, line, source } if no quote provided.
  const excerpt = quote || (source ? { loc: file ? `${file}${line ? `:${line}` : ""}` : "", code: source } : null);
  const link = openLink || (file ? { name: file, line } : null);

  return (
    <div className="tf-card tf-why tf-why-card">
      <div className="tf-card-h">
        <div className="icon" style={{ background: "var(--tf-bad-bg)", color: "var(--tf-bad)" }}>
          <Icon.Warning />
        </div>
        <div>
          <div className="title">{title}</div>
          {verdict ? <span className="tf-why-pill">{verdict}</span> : null}
        </div>
      </div>
      <div className="tf-why-body">
        {body ? <div className="tf-why-prose">{body}</div> : null}
        {excerpt ? (
          <div className="quote">
            {excerpt.loc ? <span className="loc">{excerpt.loc}</span> : null}
            <span className="snippet">{excerpt.code}</span>
          </div>
        ) : null}
        <div className="tf-why-actions">
          {link ? (
            <button className="tf-btn sm" onClick={() => onOpenLink?.(link)}>
              <Icon.Edit width="12" height="12" />
              Open {link.name}{link.line ? ` at line ${link.line}` : ""}
            </button>
          ) : null}
          {onProposeFix ? (
            <button className="tf-btn sm primary" onClick={() => onProposeFix?.()}>
              <Icon.Sparkles width="12" height="12" /> Propose fix
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/* ============ Strategy result card ============ */
export function StrategyResultCard({ title = "Verification results by strategy", strategies = [], onOpen }) {
  return (
    <div className="tf-card tf-strat-res">
      <div className="tf-card-h">
        <div className="icon"><Icon.Activity /></div>
        <div>
          <div className="title">{title}</div>
          <div className="sub">Live counts by verification method — open details only when something fails.</div>
        </div>
      </div>
      <div className="tf-strat-res-body">
        {strategies.length === 0 ? (
          <div className="tf-strat-res-empty">No strategies have reported results yet.</div>
        ) : null}
        {strategies.map((s) => {
          const status = String(s.status || "idle").toLowerCase();
          const passed = s.passed ?? null;
          const total = s.total ?? null;
          const counts = passed != null && total != null ? `${passed}/${total}` : s.detail || "";
          return (
            <button
              type="button"
              key={s.id || s.name}
              className={`tf-strat-chip ${status}`}
              onClick={() => onOpen?.(s.id || s.name)}
              title={s.detail || `${s.name} · ${status}`}
            >
              <span className="dot" />
              <span className="name">{s.name}</span>
              {counts ? <span className="counts">{counts}</span> : null}
              <span className="badge">
                {status === "pass" ? "✓" : status === "fail" ? "✗" : status === "partial" ? "◐" : status === "running" ? "⟳" : "·"}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ============ Waveform card ============ */
export function WaveformCard({ signals = [], failureAt, note }) {
  const width = 360;
  const cellW = signals[0]?.values?.length ? width / signals[0].values.length : 20;

  return (
    <div className="tf-card">
      <div className="tf-wave">
        <div className="tf-wave-h">
          <Icon.Wave width="14" height="14" />
          <span>Waveform</span>
          {failureAt ? <span className="badge">{failureAt}</span> : null}
        </div>
        <div className="tf-wave-grid">
          {signals.map((sig) => (
            <div key={sig.name} className="tf-wave-row">
              <span className="sig">{sig.name}</span>
              <svg viewBox={`0 0 ${width} 22`} preserveAspectRatio="none">
                {sig.values.map((v, i) => {
                  const x = i * cellW;
                  const isExp = sig.name?.includes("expected");
                  const stroke = isExp ? "var(--tf-good)" : sig.name?.includes("got") || sig.name?.includes("actual") ? "var(--tf-bad)" : "var(--tf-ink-2)";
                  if (v === "x") {
                    return <rect key={i} x={x} y={2} width={cellW} height={18} fill="var(--tf-surface-3)" />;
                  }
                  const y = v ? 4 : 18;
                  const nextV = sig.values[i + 1];
                  return (
                    <g key={i}>
                      <line x1={x} y1={y} x2={x + cellW} y2={y} stroke={stroke} strokeWidth="1.5" />
                      {nextV !== undefined && nextV !== v && v !== "x" && nextV !== "x" ? (
                        <line x1={x + cellW} y1={4} x2={x + cellW} y2={18} stroke={stroke} strokeWidth="1.5" />
                      ) : null}
                    </g>
                  );
                })}
              </svg>
            </div>
          ))}
        </div>
        {note ? <div className="tf-wave-foot">{note}</div> : null}
      </div>
    </div>
  );
}

/* ============ Codebase graph card ============ */
export function CodebaseGraphCard({ summary, nodeCount = 0, edgeCount = 0, onOpenFull }) {
  return (
    <div className="tf-card tf-mm-card">
      <div className="tf-card-h">
        <div className="icon"><Icon.Graph /></div>
        <div>
          <div className="title">Codebase map ready</div>
          <div className="sub">{nodeCount} nodes · {edgeCount} edges</div>
        </div>
        <span className="spacer" />
        {onOpenFull ? (
          <button type="button" className="micro-btn" onClick={() => onOpenFull?.()}>Open map</button>
        ) : null}
      </div>
      {summary ? <div className="tf-mm-desc">{summary}</div> : null}
    </div>
  );
}

/* ============ Mental model card (inline) ============ */
export function MentalModelCard({
  summary,
  modules = [],
  inferred,
  superseded = false,
  approved = false,
  onOpenFull,
  onApprove,
  onCorrect,
}) {
  const [tab, setTab] = useState("overview");
  const [correctOpen, setCorrectOpen] = useState(false);
  const [correction, setCorrection] = useState("");
  const locked = superseded || approved;

  const tabs = [
    { id: "overview", label: "Overview", Ico: Icon.MmOverview },
    { id: "modules", label: "Modules", Ico: Icon.MmModules },
    { id: "ports", label: "Ports", Ico: Icon.MmPorts },
  ];

  const submitCorrection = () => {
    const t = correction.trim();
    if (!t) return;
    onCorrect?.(t);
    setCorrection("");
    setCorrectOpen(false);
  };

  return (
    <div className={`tf-card tf-mm-card${superseded ? " tf-superseded" : ""}${approved ? " tf-mm-approved" : ""}`}>
      <div className="tf-card-h">
        <div className="icon"><Icon.Brain /></div>
        <div>
          <div className="title">How I understand it</div>
          <div className="sub">My mental model before writing code</div>
        </div>
        <span className="spacer" />
        {onOpenFull ? (
          <button className="micro-btn" onClick={() => onOpenFull?.()}>Open full view</button>
        ) : null}
      </div>
      <div className="tf-mm-body">
        {summary ? (
          <div className="tf-mm-desc">{summary}</div>
        ) : null}

        <div className="tf-mm-tabs" role="tablist" aria-label="Mental model sections">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`tf-mm-tab ${tab === t.id ? "active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              <span className="tf-mm-tab-ico" aria-hidden>
                <t.Ico width="15" height="15" strokeWidth={2} />
              </span>
              <span className="tf-mm-tab-label">{t.label}</span>
            </button>
          ))}
        </div>

        <div className="tf-mm-tab-body">
          {tab === "overview" ? (
            <ul className="tf-mm-list">
              {(inferred ? Object.entries(inferred) : []).slice(0, 6).map(([k, v]) => (
                <li key={k}>
                  <span className="k">{k}</span>
                  <span>{String(v)}</span>
                </li>
              ))}
              {!inferred ? <li><span>Awaiting analysis...</span></li> : null}
            </ul>
          ) : null}
          {tab === "modules" ? (
            <ul className="tf-mm-list">
              {modules.length === 0 ? <li><span>No modules detected yet.</span></li> : null}
              {modules.map((m, i) => (
                <li key={m.name || i}>
                  <span className="k">{m.name}</span>
                  <span>{m.role || "module"}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {tab === "ports" ? (
            <ul className="tf-mm-list">
              {modules.flatMap((m) => m.ports || []).slice(0, 10).map((p, i) => (
                <li key={i}>
                  <span className="k">{p.name}</span>
                  <span>{p.direction} {p.width ? `[${p.width}]` : ""}</span>
                </li>
              ))}
              {modules.flatMap((m) => m.ports || []).length === 0 ? (
                <li><span>No port information yet.</span></li>
              ) : null}
            </ul>
          ) : null}
        </div>

        {correctOpen && !locked ? (
          <div className="tf-mm-correct">
            <textarea
              className="tf-mm-correct-input"
              value={correction}
              onChange={(e) => setCorrection(e.target.value)}
              placeholder="What's off? e.g. 'baud should be reloadable any time, not only when FIFOs are empty'"
              rows={3}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submitCorrection();
              }}
            />
            <div className="tf-mm-correct-actions">
              <button
                type="button"
                className="tf-btn"
                onClick={() => { setCorrectOpen(false); setCorrection(""); }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="tf-btn primary"
                onClick={submitCorrection}
                disabled={!correction.trim()}
              >
                Send correction
              </button>
            </div>
          </div>
        ) : null}
      </div>

      {!locked ? (
        <div className="tf-card-actions">
          {onApprove ? (
            <button type="button" className="tf-btn primary" onClick={() => onApprove?.()}>
              Looks right — continue
            </button>
          ) : null}
          {onCorrect ? (
            <button
              type="button"
              className="tf-btn"
              onClick={() => setCorrectOpen((v) => !v)}
            >
              {correctOpen ? "Cancel correction" : "Something's off"}
            </button>
          ) : null}
        </div>
      ) : approved ? (
        <div className="tf-card-actions tf-mm-locked">
          <span className="tf-mm-locked-tag">✓ Approved — continuing</span>
        </div>
      ) : (
        <div className="tf-card-actions tf-mm-locked">
          <span className="tf-mm-locked-tag muted">Superseded</span>
        </div>
      )}
    </div>
  );
}

/* ============ Strategy card ============ */
export function StrategyCard({ title = "Verification strategy", sub, options = [], selected = [], onToggle, onConfirm }) {
  const isPicked = (id) => (selected instanceof Set ? selected.has(id) : (selected || []).includes(id));
  return (
    <div className="tf-card tf-strat-card">
      <div className="tf-card-h">
        <div className="icon"><Icon.Beaker /></div>
        <div>
          <div className="title">{title}</div>
          {sub ? <div className="sub">{sub}</div> : null}
        </div>
      </div>
      <div className="tf-strat-body">
        {options.map((opt) => (
          <button
            key={opt.id}
            className={`tf-strat-row ${isPicked(opt.id) ? "picked" : ""}`}
            onClick={() => onToggle?.(opt.id)}
          >
            <span className="check">{isPicked(opt.id) ? <Icon.Check width="12" height="12" /> : null}</span>
            <div className="body">
              <div className="name">
                {opt.name}
                {opt.recommended ? <span className="reco">Recommended</span> : null}
                {opt.skip ? <span className="skip">Skip</span> : null}
              </div>
              {opt.description ? <div className="desc">{opt.description}</div> : null}
              {opt.why ? <div className="why">{opt.why}</div> : null}
            </div>
          </button>
        ))}
      </div>
      {onConfirm ? (
        <div className="tf-card-actions">
          <button className="tf-btn primary" onClick={() => onConfirm()}>Continue with selected</button>
        </div>
      ) : null}
    </div>
  );
}

/* ============ Plan approval (toggleable tests) ============ */
export function PlanApprovalCard({ title = "Test plan", sub, tests = [], onToggle, onApprove }) {
  const onCount = tests.filter((t) => t.on).length;
  return (
    <div className="tf-card tf-papp-card">
      <div className="tf-card-h">
        <div className="icon"><Icon.CheckCircle /></div>
        <div>
          <div className="title">{title}</div>
          {sub ? <div className="sub">{sub}</div> : null}
        </div>
      </div>
      <div className="tf-papp-body">
        {tests.map((t) => (
          <button
            key={t.id}
            className={`tf-papp-row ${t.on ? "on" : "off"}`}
            onClick={() => onToggle?.(t.id)}
          >
            <span className="check">{t.on ? <Icon.Check width="12" height="12" /> : null}</span>
            <span className="label">{t.label}</span>
            {t.kind ? <span className="kind">{t.kind}</span> : null}
          </button>
        ))}
      </div>
      <div className="tf-papp-summary">
        <strong>{onCount}</strong> of <strong>{tests.length}</strong> tests selected
      </div>
      {onApprove ? (
        <div className="tf-card-actions">
          <button className="tf-btn primary" onClick={() => onApprove()}>Approve and build</button>
        </div>
      ) : null}
    </div>
  );
}

/* ============ Toolchain card ============ */
export function ToolchainCard({ items = [] }) {
  return (
    <div className="tf-card tf-tool-card">
      <span className="lbl">Toolchain</span>
      {items.map((it) => (
        <span key={it.name} className={`tf-tool-badge ${it.ok ? "ok" : "bad"}`}>
          {it.ok ? <Icon.Check width="12" height="12" /> : <Icon.X width="12" height="12" />}
          {it.name}
        </span>
      ))}
    </div>
  );
}

/* ============ Coverage card ============ */
export function CoverageCard({ rows = [], title = "Coverage", sub }) {
  return (
    <div className="tf-card tf-cov-card">
      <div className="h">
        <div className="icon"><Icon.Activity /></div>
        <div>
          <div className="title">{title}</div>
          {sub ? <div className="sub">{sub}</div> : null}
        </div>
      </div>
      <div className="tf-cov-bars">
        {rows.map((r) => {
          const pct = Math.max(0, Math.min(100, Number(r.pct || 0)));
          const kind = r.kind || (pct < 40 ? "low" : pct < 75 ? "mid" : "good");
          return (
            <div key={r.name} className="tf-cov-row">
              <span className="name">{r.name}</span>
              <span className="bar"><span className={`fill ${kind === "good" ? "" : kind}`} style={{ width: `${pct}%` }} /></span>
              <span className="pct">{pct}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ============ Schematic card (inline placeholder; full overlay uses RTLVisualizer) ============ */
export function SchematicCard({ note, modules = [], onOpenFull }) {
  return (
    <div className="tf-card tf-sch-card">
      <div className="h">
        <div className="icon"><Icon.Cpu /></div>
        <div className="title">Schematic preview</div>
        <span className="spacer" />
        {onOpenFull ? <button className="tf-btn sm" onClick={() => onOpenFull?.()}>Open visualizer</button> : null}
      </div>
      <div className="canvas">
        {modules.length ? (
          <svg viewBox="0 0 600 200" style={{ width: "100%", height: 180, display: "block" }}>
            {modules.slice(0, 5).map((m, i) => {
              const cols = Math.min(5, modules.length);
              const x = 30 + (i % cols) * 110;
              const y = 60 + Math.floor(i / cols) * 80;
              return (
                <g key={i}>
                  <rect x={x} y={y} width={90} height={50} rx={8}
                    fill="var(--tf-surface)" stroke="var(--tf-line-strong)" strokeWidth={1.2} />
                  <text x={x + 45} y={y + 30} textAnchor="middle"
                    fill="var(--tf-ink)" style={{ fontSize: 11, fontFamily: "Inter, sans-serif" }}>
                    {m.name}
                  </text>
                </g>
              );
            })}
          </svg>
        ) : (
          <div style={{ color: "var(--tf-ink-3)", fontSize: 13, padding: "32px 12px", textAlign: "center" }}>
            {note || "No module layout yet. Generate or upload RTL to see a quick schematic."}
          </div>
        )}
      </div>
    </div>
  );
}

/* ============ Agent beat (live run telemetry rolled into one card) ============ */
function formatBeatElapsed(ms) {
  if (ms == null || ms < 0) return "—";
  const s = Math.floor(ms / 1000);
  const mm = Math.floor(s / 60);
  const ss = s % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

function classifyAgentStatus(agent) {
  const t = `${agent.status || ""} ${agent.last_event_phase || ""}`.toLowerCase();
  if (/fail|error|broke/.test(t)) return "bad";
  if (/run|active|busy|simulat|verif|work|generat/.test(t)) return "now";
  if (/done|complete|pass|ok|idle/.test(t)) return "done";
  return "todo";
}

export function AgentBeatCard({
  runId,
  runStatus,
  startedAt,
  agents = [],
  collapsed = false,
  onOpenRun,
}) {
  const [elapsed, setElapsed] = React.useState(0);
  const status = String(runStatus || "running").toLowerCase();
  const terminal = ["completed", "passed", "failed", "cancelled", "interrupted"].includes(status);
  const isFail = ["failed", "cancelled", "interrupted"].includes(status);

  React.useEffect(() => {
    if (terminal || !startedAt) {
      if (startedAt) setElapsed(Date.now() - startedAt);
      return undefined;
    }
    const tick = () => setElapsed(Date.now() - startedAt);
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt, terminal]);

  const active = agents.filter((a) => classifyAgentStatus(a) === "now").length;

  if (collapsed || (terminal && agents.length > 0)) {
    const summary = terminal
      ? isFail
        ? `Pipeline failed · ${agents.length} agent${agents.length === 1 ? "" : "s"} · ${formatBeatElapsed(elapsed)}`
        : `Pipeline done · ${agents.length} agent${agents.length === 1 ? "" : "s"} · ${formatBeatElapsed(elapsed)}`
      : `Pipeline running · ${active}/${agents.length} active · ${formatBeatElapsed(elapsed)}`;
    return (
      <button
        type="button"
        className={`tf-card tf-agentbeat compact ${isFail ? "bad" : terminal ? "good" : "busy"}`}
        onClick={() => onOpenRun?.()}
      >
        <span className={`tf-agentbeat-dot ${isFail ? "bad" : terminal ? "good" : "busy"}`} />
        <span className="tf-agentbeat-summary">{summary}</span>
        {runId ? <span className="tf-agentbeat-rid">{String(runId).slice(0, 8)}</span> : null}
      </button>
    );
  }

  return (
    <div className={`tf-card tf-agentbeat ${terminal ? (isFail ? "bad" : "good") : "busy"}`}>
      <div className="tf-agentbeat-h">
        <span className={`tf-agentbeat-dot ${terminal ? (isFail ? "bad" : "good") : "busy"}`} />
        <span className="tf-agentbeat-title">
          {terminal ? (isFail ? "Pipeline failed" : "Pipeline complete") : "Pipeline running"}
        </span>
        <span className="tf-agentbeat-meta">
          {active}/{agents.length} active · {formatBeatElapsed(elapsed)}
        </span>
        {onOpenRun ? (
          <button className="tf-link" onClick={() => onOpenRun()}>Open run</button>
        ) : null}
      </div>
      <div className="tf-agentbeat-rows">
        {agents.length === 0 ? (
          <div className="tf-agentbeat-empty">Waiting for the pipeline to report in…</div>
        ) : (
          agents.map((a) => {
            const cls = classifyAgentStatus(a);
            const detail = a.last_event_message || a.last_event_phase || a.status || "";
            return (
              <div key={a.id || a.name} className={`tf-agentbeat-row ${cls}`}>
                <span className="mark" aria-hidden>
                  {cls === "done" ? "✓" : cls === "bad" ? "✗" : cls === "now" ? "•" : "·"}
                </span>
                <span className="name">{a.name || a.id || "Agent"}</span>
                {detail ? <span className="detail">{String(detail).slice(0, 96)}</span> : null}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

/* ============ Task list (live tasks the agent declared) ============ */
export function TaskListCard({ tasks = [], title }) {
  const total = tasks.length;
  const done = tasks.filter((t) => /done|complete|pass/i.test(t.state || "")).length;
  const active = tasks.findIndex((t) => /run|active|now|progress/i.test(t.state || ""));
  const allDone = total > 0 && done === total;
  const [open, setOpen] = useState(!allDone);

  React.useEffect(() => {
    if (allDone) setOpen(false);
  }, [allDone]);

  return (
    <div className={`tf-card tf-tasklist${allDone ? " done" : ""}`}>
      <button type="button" className="tf-tasklist-h" onClick={() => setOpen((v) => !v)}>
        <Icon.ChevronRight
          width="11"
          height="11"
          className={open ? "rot" : ""}
        />
        <span className="tf-tasklist-title">
          {title || (allDone ? "Tasks" : "Working through tasks")}
        </span>
        <span className="tf-tasklist-meta">{done}/{total} done</span>
      </button>
      {open ? (
        <ul className="tf-tasklist-rows">
          {tasks.map((t, i) => {
            const state = String(t.state || "").toLowerCase();
            const cls =
              /done|complete|pass/.test(state) ? "done"
              : /run|active|now|progress/.test(state) ? "now"
              : /fail|error|bad/.test(state) ? "bad"
              : "todo";
            return (
              <li key={t.id || i} className={`tf-tasklist-row ${cls}`}>
                <span className="mark" aria-hidden>
                  {cls === "done" ? "✓" : cls === "now" ? "•" : cls === "bad" ? "✗" : "·"}
                </span>
                <span className="label">{t.label || t.title || `Task ${i + 1}`}</span>
                {t.detail && (cls === "now" || cls === "bad") ? (
                  <span className="detail">{t.detail}</span>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

/* ============ Turn summary (quiet recap of long agent turns) ============ */
export function TurnSummaryCard({ durationMs, toolCalls = 0, filesWritten = 0, headline }) {
  const s = Math.max(0, Math.floor((durationMs || 0) / 1000));
  const mm = Math.floor(s / 60);
  const ss = s % 60;
  const time = `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  const parts = [];
  if (filesWritten > 0) parts.push(`${filesWritten} file${filesWritten === 1 ? "" : "s"}`);
  if (toolCalls > 0) parts.push(`${toolCalls} tool call${toolCalls === 1 ? "" : "s"}`);
  parts.push(time);
  return (
    <div className="tf-card tf-turnsummary">
      <span className="tf-turnsummary-dot" aria-hidden />
      <span className="tf-turnsummary-text">
        {headline ? <strong>{headline}</strong> : <strong>Turn complete</strong>}
        <span className="tf-turnsummary-sep"> · </span>
        {parts.join(" · ")}
      </span>
    </div>
  );
}

/* ============ Renderer ============ */
export function ThreadItemRenderer({ item, handlers = {} }) {
  if (!item || !item.kind) return null;
  const { kind, ts, payload } = item;

  switch (kind) {
    case "user":
      return (
        <UserBubble
          text={payload.text}
          ts={ts}
          tokenUsage={payload.tokenUsage}
          onEdit={
            handlers.onEditUserMessage && item.turnId
              ? () => handlers.onEditUserMessage(item)
              : undefined
          }
        />
      );
    case "said":
      return (
        <Said
          text={payload.text}
          ts={ts}
          tokenUsage={payload.tokenUsage}
          streaming={payload.streaming}
        />
      );
    case "thinking":
      return <Thinking text={payload.text} />;
    case "break":
      return <SectionBreak label={payload.label} />;
    case "ask":
      return (
        <AskCard
          {...payload}
          onChoose={(cid) => handlers.onAskChoose?.(item.id, cid)}
        />
      );
    case "plan":
      return (
        <PlanCard
          {...payload}
          cadenceConnected={handlers.cadenceConnected}
          onPlanAction={(actionId, extra) => handlers.onPlanAction?.(item.id, actionId, extra)}
        />
      );
    case "stagedPrep":
      return (
        <StagedPrepareCard
          {...payload}
          onRecommend={() => handlers.onStagedRecommend?.(item.id)}
        />
      );
    case "stagedRec":
      return (
        <StagedRecommendCard
          {...payload}
          onPickStrategy={(sid) => handlers.onStagedPickStrategy?.(item.id, sid)}
        />
      );
    case "file":
      return (
        <FileCard
          {...payload}
          onOpen={() => handlers.onOpenFile?.(item.id, payload)}
        />
      );
    case "diff": {
      const patchId = payload.applies_patch_id;
      return (
        <DiffCard
          {...payload}
          onApply={() => {
            if (patchId && handlers.onApplyPatch) {
              handlers.onApplyPatch(item.id, patchId, payload);
            } else {
              handlers.onDiffApply?.(item.id, payload);
            }
          }}
          onReject={(reason) => {
            if (patchId && handlers.onRejectPatch) {
              handlers.onRejectPatch(item.id, patchId, payload, reason);
            } else {
              handlers.onDiffReject?.(item.id, payload, reason);
            }
          }}
          onEdit={() => handlers.onDiffEdit?.(item.id, payload)}
        />
      );
    }
    case "run":
      return (
        <RunCard
          {...payload}
          onCancel={handlers.onRunCancel}
          onDownload={handlers.onRunDownload}
          onUploadLog={(runPayload) => handlers.onRunUploadLog?.(item.id, runPayload || payload)}
        />
      );
    case "cadenceRun":
      return <CadenceRunCard {...payload} />;
    case "designBugReport":
      return (
        <DesignBugReportCard
          {...payload}
          onOpenReport={() => handlers.onOpenFile?.(item.id, {
            artifactId: payload.artifactId,
            name: payload.report?.filename || "verification_closure.md",
            language: "markdown",
          })}
          onOpenSpec={(ref) => handlers.onOpenFile?.(item.id, {
            artifactId: ref.artifact_id,
            name: ref.file || "specification",
            line: ref.line,
            page: ref.page,
          })}
          onOpenRtl={(ref) => handlers.onOpenFile?.(item.id, {
            artifactId: ref.artifact_id,
            name: ref.file || "RTL evidence",
            line: ref.line,
          })}
          onDownloadEvidence={payload.artifactIds?.length ? () => handlers.onRunDownload?.(payload.runId, {
            artifactIds: payload.artifactIds,
            filename: `xcelium_evidence_${payload.runId || "run"}.zip`,
          }) : undefined}
        />
      );
    case "verdict":
      return (
        <VerdictCard
          {...payload}
          onAction={(actionId) => handlers.onVerdictAction?.(item.id, actionId, payload)}
        />
      );
    case "why":
      return (
        <WhyCard
          {...payload}
          onOpenLink={(lnk) => handlers.onOpenFile?.(item.id, lnk)}
          onProposeFix={handlers.onProposeFix ? () => handlers.onProposeFix(item.id, payload) : undefined}
        />
      );
    case "strategyResult":
      return (
        <StrategyResultCard
          {...payload}
          onOpen={(sid) => handlers.onOpenDashboard?.(sid)}
        />
      );
    case "waveform":
      return <WaveformCard {...payload} />;
    case "codebaseGraph":
      return (
        <CodebaseGraphCard
          {...payload}
          onOpenFull={() => handlers.onOpenCodebaseGraph?.(item.id, payload)}
        />
      );
    case "mentalModel":
      return (
        <MentalModelCard
          {...payload}
          onOpenFull={() => handlers.onOpenMentalModel?.(item.id, payload)}
          onApprove={handlers.onMentalModelApprove ? () => handlers.onMentalModelApprove(item.id, payload) : undefined}
          onCorrect={handlers.onMentalModelCorrect ? (text) => handlers.onMentalModelCorrect(item.id, text) : undefined}
        />
      );
    case "strategy":
      return (
        <StrategyCard
          {...payload}
          onToggle={(oid) => handlers.onStrategyToggle?.(item.id, oid)}
          onConfirm={payload.locked ? undefined : () => handlers.onStrategyConfirm?.(item.id)}
        />
      );
    case "planApproval":
      return (
        <PlanApprovalCard
          {...payload}
          onToggle={(tid) => handlers.onPlanApprovalToggle?.(item.id, tid)}
          onApprove={payload.locked ? undefined : () => handlers.onPlanApprovalApprove?.(item.id)}
        />
      );
    case "toolchain":
      return <ToolchainCard {...payload} />;
    case "coverage":
      return <CoverageCard {...payload} />;
    case "schematic":
      return (
        <SchematicCard
          {...payload}
          onOpenFull={() => handlers.onOpenSchematic?.(item.id, payload)}
        />
      );
    case "agentBeat":
      return (
        <AgentBeatCard
          {...payload}
          onOpenRun={() => handlers.onOpenDashboard?.(item.id, payload)}
        />
      );
    case "turnSummary":
      return <TurnSummaryCard {...payload} />;
    case "taskList":
      return <TaskListCard {...payload} />;
    default:
      return null;
  }
}

export default ThreadItemRenderer;
