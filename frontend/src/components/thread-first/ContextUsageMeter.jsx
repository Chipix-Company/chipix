import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

export function formatTokenCount(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n < 0) return "0";
  if (n >= 1000000) return `${(n / 1000000).toFixed(n >= 10000000 ? 0 : 1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K`;
  return String(Math.round(n));
}

function formatTokenCountPrecise(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n < 0) return "0";
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(Math.round(n));
}

function ringTone(percent) {
  if (percent >= 90) return "critical";
  if (percent >= 75) return "warn";
  return "ok";
}

function ContextRing({ percent, size = 26 }) {
  const stroke = 2.25;
  const r = (size - stroke * 2) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const circumference = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(100, percent));
  const offset = circumference * (1 - clamped / 100);

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      className={`tf-context-ring-svg tone-${ringTone(clamped)}`}
      aria-hidden
    >
      <circle
        cx={cx}
        cy={cy}
        r={r}
        className="tf-context-ring-track"
        strokeWidth={stroke}
        fill="none"
      />
      <circle
        cx={cx}
        cy={cy}
        r={r}
        className="tf-context-ring-progress"
        strokeWidth={stroke}
        fill="none"
        strokeDasharray={circumference}
        strokeDashoffset={offset}
        strokeLinecap="round"
        transform={`rotate(-90 ${cx} ${cy})`}
      />
    </svg>
  );
}

const THEME_SHELL_SELECTOR = '[data-ui-shell="thread-first"]';

function resolveThemePortal(anchorEl) {
  if (!anchorEl) return document.body;
  return anchorEl.closest(THEME_SHELL_SELECTOR) || document.body;
}

/**
 * Cursor-style context control: circular ring in the composer toolbar;
 * click opens the detailed breakdown panel above.
 */
function useFixedPanelPosition(open, enabled, anchorRef) {
  const [style, setStyle] = useState(null);

  useLayoutEffect(() => {
    if (!open || !enabled || !anchorRef.current) {
      setStyle(null);
      return undefined;
    }

    const update = () => {
      const btn = anchorRef.current?.getBoundingClientRect();
      if (!btn) return;
      const panelW = Math.min(320, Math.max(260, window.innerWidth - 24));
      const right = Math.max(12, window.innerWidth - btn.right);
      setStyle({
        position: "fixed",
        right,
        bottom: window.innerHeight - btn.top + 10,
        width: panelW,
        zIndex: 120,
      });
    };

    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, enabled, anchorRef]);

  return style;
}

export default function ContextUsageMeter({
  tokenContext,
  className = "",
  /** Use in narrow rails (IDE side panel) so the panel is not clipped or sized to the ring wrap. */
  fixedPanel = false,
}) {
  const wrapRef = useRef(null);
  const anchorRef = useRef(null);
  const panelRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [portalTarget, setPortalTarget] = useState(() => document.body);
  const fixedPanelStyle = useFixedPanelPosition(open, fixedPanel, anchorRef);

  useLayoutEffect(() => {
    if (!open || !fixedPanel) return;
    const anchor = anchorRef.current || wrapRef.current;
    setPortalTarget(resolveThemePortal(anchor));
  }, [open, fixedPanel]);

  const used = Number(tokenContext?.usedTokens || 0);
  const budget = Number(tokenContext?.budgetTokens || 0);
  const percent = Math.max(0, Math.min(100, Number(tokenContext?.percent || 0)));
  const projectTotal = Number(tokenContext?.projectTotalTokens || 0);

  const breakdown = useMemo(() => {
    const rows = Array.isArray(tokenContext?.breakdown) ? tokenContext.breakdown : [];
    return rows.filter((row) => Number(row?.tokens || 0) > 0);
  }, [tokenContext?.breakdown]);

  const segmentWidths = useMemo(() => {
    const total = breakdown.reduce((sum, row) => sum + Number(row.tokens || 0), 0) || used || 1;
    return breakdown.map((row) => ({
      ...row,
      sharePct: (Number(row.tokens || 0) / total) * 100,
    }));
  }, [breakdown, used]);

  const toggle = useCallback(() => {
    setOpen((v) => !v);
  }, []);

  const close = useCallback(() => {
    setOpen(false);
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      const target = e.target;
      if (wrapRef.current?.contains(target)) return;
      if (fixedPanel && panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, fixedPanel]);

  if (!tokenContext) return null;

  const percentLabel = percent >= 10 ? `${Math.round(percent)}%` : percent > 0 ? "<1%" : "0%";

  const panelEl = open ? (
    <div
      ref={panelRef}
      className={`tf-context-panel${fixedPanel ? " tf-context-panel--fixed" : ""}`}
      style={fixedPanel ? fixedPanelStyle || undefined : undefined}
      role="dialog"
      aria-label="Context usage breakdown"
    >
      <div className="tf-context-panel-top">
        <span className="tf-context-panel-title">Context</span>
        <button
          type="button"
          className="tf-context-panel-close"
          onClick={close}
          aria-label="Close context panel"
        >
          ×
        </button>
      </div>
      <div className="tf-context-panel-summary">
        <span className="tf-context-panel-pct">{percentLabel} full</span>
        <span className="tf-context-panel-total">
          ~{formatTokenCountPrecise(used)} / {formatTokenCount(budget)} tokens
        </span>
      </div>
      <div className="tf-context-panel-bar" aria-hidden>
        {segmentWidths.map((row) => (
          <span
            key={row.id || row.label}
            className="tf-context-panel-seg"
            style={{
              width: `${Math.max(row.sharePct, row.tokens > 0 ? 0.6 : 0)}%`,
              background: row.color || "var(--tf-ink-3)",
            }}
            title={`${row.label}: ${formatTokenCount(row.tokens)}`}
          />
        ))}
      </div>
      <ul className="tf-context-panel-rows">
        {segmentWidths.map((row) => (
          <li key={row.id || row.label}>
            <span
              className="tf-context-panel-swatch"
              style={{ background: row.color || "var(--tf-ink-3)" }}
              aria-hidden
            />
            <span className="tf-context-panel-row-label">{row.label}</span>
            <span className="tf-context-panel-row-val">{formatTokenCount(row.tokens)}</span>
          </li>
        ))}
      </ul>
      {tokenContext?.isEstimated ? (
        <p className="tf-context-panel-note">Estimated from text length until provider usage is saved.</p>
      ) : null}
      {projectTotal > 0 ? (
        <p className="tf-context-panel-note">
          Project lifetime: {formatTokenCount(projectTotal)} tokens (all threads).
        </p>
      ) : null}
    </div>
  ) : null;

  return (
    <div
      ref={wrapRef}
      className={`tf-context-meter-wrap${open ? " open" : ""}${className ? ` ${className}` : ""}`}
    >
      {fixedPanel && panelEl
        ? createPortal(panelEl, portalTarget)
        : panelEl}

      <button
        ref={anchorRef}
        type="button"
        className="tf-context-ring-btn"
        onClick={toggle}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={`Context ${percentLabel} full · ${formatTokenCount(used)} / ${formatTokenCount(budget)}`}
        aria-label={open ? "Hide context breakdown" : `Context ${percentLabel} full, show breakdown`}
      >
        <ContextRing percent={percent} />
      </button>
    </div>
  );
}
