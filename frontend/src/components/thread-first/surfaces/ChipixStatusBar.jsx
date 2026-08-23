/**
 * ChipixStatusBar — the always-on bottom strip that makes the app feel
 * like a real desktop app.
 *
 * Inspired by VS Code's status bar and Slack's footer: every section
 * answers a question the user might ask without making them ask it.
 *   • am I connected?  → pulse + label
 *   • what lens am I in?  → mode chip
 *   • is something happening right now?  → agent activity slot
 *   • how did the last run go?  → verdict pill (click → dashboard)
 *   • is sound on?  → speaker toggle
 *   • what time is it?  → clock
 *
 * Sections are clickable only when there's a destination. Hover affordances
 * fire the soft tick sound. The bar is mode-tinted so the lens choice
 * threads through the whole window.
 */

import React, { useEffect, useMemo, useState, useCallback } from "react";
import Icon from "../icons";
import { CHIPIX_MODES } from "../useChipixMode";
import {
  isSoundEnabled,
  setSoundEnabled,
  subscribeSoundEnabled,
  playTick,
} from "../useChipixSound";

function formatClock(date) {
  const h = String(date.getHours()).padStart(2, "0");
  const m = String(date.getMinutes()).padStart(2, "0");
  return `${h}:${m}`;
}

function formatElapsed(ms) {
  if (ms == null) return "";
  const s = Math.max(0, Math.floor(ms / 1000));
  const mm = Math.floor(s / 60);
  const ss = s % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const next = 60000 - (Date.now() % 60000); // align to minute boundary
    const t = setTimeout(() => setNow(new Date()), next);
    return () => clearTimeout(t);
  }, [now]);
  return now;
}

function useSoundFlag() {
  const [on, setOn] = useState(() => isSoundEnabled());
  useEffect(() => subscribeSoundEnabled(setOn), []);
  return [on, (v) => setSoundEnabled(v)];
}

export default function ChipixStatusBar({
  // backend health
  backendStatus = "ok",        // "ok" | "busy" | "bad"
  backendLabel = "Connected",
  // lens
  mode = CHIPIX_MODES.DESIGN,
  derivedPhase = "empty",
  // agent activity
  busy = false,
  activityText = null,         // e.g. "Generating uart_top.sv"
  streamStartedAt = null,      // ms epoch
  // last verdict (optional)
  verdict = null,              // { ok, summary, sub, onOpen }
  // project label (for far-left "ID")
  projectLabel = null,
  // Cadence Xcelium status chip (always visible) — { label, tone, title }
  cadenceStatus = null,
  onConfigureCadence,
  // click handlers
  onOpenDashboard,
  onOpenPalette,
  codebaseGraphStatus = null,
  onOpenCodebaseGraph,
}) {
  const clock = useClock();
  const [elapsedMs, setElapsedMs] = useState(0);
  const [soundOn, setSoundOn] = useSoundFlag();

  // Tick the activity timer while busy.
  useEffect(() => {
    if (!busy || !streamStartedAt) { setElapsedMs(0); return undefined; }
    const tick = () => setElapsedMs(Date.now() - streamStartedAt);
    tick();
    const id = setInterval(tick, 500);
    return () => clearInterval(id);
  }, [busy, streamStartedAt]);

  const phaseLabel = useMemo(() => {
    switch (derivedPhase) {
      case "empty": return "no design yet";
      case "designing": return "designing";
      case "mental-model": return "mental model";
      case "ready-to-verify": return "ready to verify";
      default: return derivedPhase;
    }
  }, [derivedPhase]);

  const handleSoundToggle = useCallback(() => {
    const next = !soundOn;
    setSoundOn(next);
    // Audible feedback only when turning ON (so toggling off doesn't lie).
    if (next) playTick({ pitch: "high" });
  }, [soundOn, setSoundOn]);

  const handleVerdictClick = useCallback(() => {
    if (verdict?.onOpen) { playTick({ pitch: "high" }); verdict.onOpen(); }
    else if (onOpenDashboard) { playTick({ pitch: "high" }); onOpenDashboard(); }
  }, [verdict, onOpenDashboard]);

  const handleProjectClick = useCallback(() => {
    if (onOpenPalette) { playTick({ pitch: "low" }); onOpenPalette(); }
  }, [onOpenPalette]);

  return (
    <footer
      className="tf-status-bar"
      role="contentinfo"
      data-mode={mode}
      data-backend={backendStatus}
    >
      {/* LEFT — backend pulse */}
      <div
        className={`tf-status-sec tf-status-conn tf-status-conn-${backendStatus}`}
        title={`Backend ${backendStatus}`}
      >
        <span className="tf-status-pulse" aria-hidden />
        <span className="tf-status-label">{backendLabel}</span>
      </div>

      {/* Project — palette shortcut */}
      {projectLabel ? (
        <button
          type="button"
          className="tf-status-sec tf-status-proj"
          onClick={handleProjectClick}
          title="Jump to anything (⌘K)"
        >
          <span className="tf-status-divider" aria-hidden>·</span>
          <span className="tf-status-label">{projectLabel}</span>
        </button>
      ) : null}

      {/* Mode chip — passive lens indicator */}
      <div className={`tf-status-sec tf-status-mode tf-status-mode-${mode}`}>
        <span className="tf-status-divider" aria-hidden>·</span>
        <span className="tf-status-mode-dot" aria-hidden />
        <span className="tf-status-label">
          <strong>{mode === CHIPIX_MODES.VERIFY ? "Verify" : "Design"}</strong>
          <span className="tf-status-mode-phase">{phaseLabel}</span>
        </span>
      </div>

      {/* Cadence Xcelium — always-visible tool status chip; click to configure */}
      {cadenceStatus ? (
        onConfigureCadence ? (
          <button
            type="button"
            className={`tf-status-sec tf-status-cadence tf-status-cadence-${cadenceStatus.tone || "off"}`}
            title={`${cadenceStatus.title || cadenceStatus.label} — click to configure`}
            onClick={() => { playTick({ pitch: "low" }); onConfigureCadence(); }}
          >
            <span className="tf-status-divider" aria-hidden>·</span>
            <span className="tf-status-cadence-dot" aria-hidden />
            <span className="tf-status-label">{cadenceStatus.label}</span>
          </button>
        ) : (
          <div
            className={`tf-status-sec tf-status-cadence tf-status-cadence-${cadenceStatus.tone || "off"}`}
            title={cadenceStatus.title || cadenceStatus.label}
          >
            <span className="tf-status-divider" aria-hidden>·</span>
            <span className="tf-status-cadence-dot" aria-hidden />
            <span className="tf-status-label">{cadenceStatus.label}</span>
          </div>
        )
      ) : null}

      {/* CENTER — activity slot */}
      <div className="tf-status-activity" aria-live="polite">
        {busy ? (
          <>
            <span className="tf-status-spin" aria-hidden />
            <span className="tf-status-activity-text">
              {activityText || "Working"}
            </span>
            {streamStartedAt ? (
              <span className="tf-status-activity-time">
                {formatElapsed(elapsedMs)}
              </span>
            ) : null}
          </>
        ) : null}
      </div>

      {codebaseGraphStatus?.status === "building" ? (
        <button
          type="button"
          className="tf-status-sec tf-status-graph building"
          onClick={() => { if (onOpenCodebaseGraph) { playTick({ pitch: "high" }); onOpenCodebaseGraph(); } }}
          title="Codebase map is building"
        >
          <Icon.Graph width="11" height="11" />
          <span className="tf-status-label">Mapping codebase…</span>
        </button>
      ) : null}
      {codebaseGraphStatus?.status === "ready" ? (
        <button
          type="button"
          className="tf-status-sec tf-status-graph ready"
          onClick={() => { if (onOpenCodebaseGraph) { playTick({ pitch: "high" }); onOpenCodebaseGraph(); } }}
          title="Open codebase map"
        >
          <Icon.Graph width="11" height="11" />
          <span className="tf-status-label">Codebase map</span>
        </button>
      ) : null}

      {/* RIGHT — verdict pill */}
      {verdict ? (
        <button
          type="button"
          className={`tf-status-sec tf-status-verdict ${verdict.ok ? "ok" : "bad"}`}
          onClick={handleVerdictClick}
          title="Open dashboard"
        >
          {verdict.ok ? (
            <Icon.Check width="11" height="11" />
          ) : (
            <Icon.Warning width="11" height="11" />
          )}
          <span className="tf-status-label">
            <strong>{verdict.summary}</strong>
            {verdict.sub ? <span className="tf-status-verdict-sub">{verdict.sub}</span> : null}
          </span>
        </button>
      ) : null}

      {/* Sound toggle */}
      <button
        type="button"
        className={`tf-status-sec tf-status-sound ${soundOn ? "on" : "off"}`}
        onClick={handleSoundToggle}
        title={soundOn ? "Sound on — click to mute" : "Sound off — click to enable"}
        aria-pressed={soundOn}
      >
        <SoundGlyph on={soundOn} />
      </button>

      {/* Clock */}
      <div className="tf-status-sec tf-status-clock" aria-label="Local time">
        <span className="tf-status-label">{formatClock(clock)}</span>
      </div>
    </footer>
  );
}

function SoundGlyph({ on }) {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M11 5L6 9H3v6h3l5 4z" />
      {on ? (
        <>
          <path d="M15.5 8.5a4 4 0 0 1 0 7" />
          <path d="M18.5 5.5a8 8 0 0 1 0 13" />
        </>
      ) : (
        <>
          <line x1="22" y1="9" x2="16" y2="15" />
          <line x1="16" y1="9" x2="22" y2="15" />
        </>
      )}
    </svg>
  );
}
