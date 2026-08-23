/**
 * ChipixModeToggle — the floating Design ↔ Verify lens switch.
 *
 * Visual: a single rounded pill, ~280px wide, floating above the composer,
 * bottom-centered. Two labels, an animated thumb behind them, a soft glow
 * tinted to the active mode. The thumb moves with a spring-feel cubic
 * easing so the switch *settles* rather than slides — the animation is the
 * affordance.
 *
 * Interactions:
 *   • click a label, the thumb glides to that side
 *   • keyboard ←/→ flips, Space/Enter toggles
 *   • on every transition: tick sound + 6ms haptic
 *   • when gated (verify requested but mental model not done), the thumb
 *     still moves — we never lie about state — but a small ghost row drops
 *     down with the gate reason, so the user knows the next legit step.
 */

import React, { useCallback, useEffect, useRef } from "react";
import { CHIPIX_MODES } from "../useChipixMode";
import { playTick, pulseHaptic } from "../useChipixSound";

export default function ChipixModeToggle({
  mode,
  onChange,
  isGated = false,
  gateReason = null,
  hidden = false,
}) {
  const rootRef = useRef(null);

  const flipTo = useCallback((next) => {
    if (next !== CHIPIX_MODES.DESIGN && next !== CHIPIX_MODES.VERIFY) return;
    if (next === mode) return;
    playTick({ pitch: next === CHIPIX_MODES.VERIFY ? "high" : "low" });
    pulseHaptic(6);
    onChange?.(next);
  }, [mode, onChange]);

  // Keyboard nav when the toggle is focused
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const handler = (e) => {
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        flipTo(CHIPIX_MODES.DESIGN);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        flipTo(CHIPIX_MODES.VERIFY);
      } else if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        flipTo(mode === CHIPIX_MODES.DESIGN ? CHIPIX_MODES.VERIFY : CHIPIX_MODES.DESIGN);
      }
    };
    el.addEventListener("keydown", handler);
    return () => el.removeEventListener("keydown", handler);
  }, [mode, flipTo]);

  if (hidden) return null;

  const isVerify = mode === CHIPIX_MODES.VERIFY;

  return (
    <div
      className={`tf-mode-toggle-wrap${isGated ? " gated" : ""}`}
      data-tour="mode-toggle"
      data-mode={mode}
      aria-hidden={false}
    >
      <div
        ref={rootRef}
        className="tf-mode-toggle"
        role="radiogroup"
        tabIndex={0}
        aria-label="Switch focus between design and verification"
        data-mode={mode}
      >
        <span className="tf-mode-thumb" aria-hidden />
        <button
          type="button"
          role="radio"
          aria-checked={!isVerify}
          className={`tf-mode-opt${!isVerify ? " active" : ""}`}
          onClick={() => flipTo(CHIPIX_MODES.DESIGN)}
          tabIndex={-1}
        >
          <span className="tf-mode-dot tf-mode-dot--design" aria-hidden />
          <span className="tf-mode-label">Design</span>
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={isVerify}
          className={`tf-mode-opt${isVerify ? " active" : ""}`}
          onClick={() => flipTo(CHIPIX_MODES.VERIFY)}
          tabIndex={-1}
        >
          <span className="tf-mode-dot tf-mode-dot--verify" aria-hidden />
          <span className="tf-mode-label">Verify</span>
        </button>
      </div>
      {isGated && gateReason ? (
        <div className="tf-mode-gate" role="status" aria-live="polite">
          <span className="tf-mode-gate-dot" aria-hidden />
          <span className="tf-mode-gate-text">{gateReason}</span>
        </div>
      ) : null}
    </div>
  );
}
