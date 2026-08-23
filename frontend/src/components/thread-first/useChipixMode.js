/**
 * useChipixMode — the user's current focus lens.
 *
 * First-principles separation:
 *   • PHASE  = the system's truth (derived from designLifecycle / run state).
 *              Built and owned elsewhere; this hook just reads it.
 *   • MODE   = the user's chosen lens — what kind of help they want right now.
 *              Drives placeholder text, suggestion chips, welcome starters,
 *              and the tint on the thread accent. Persisted per project.
 *
 * The toggle moves the lens. The gate still bites: flipping to `verify` when
 * the mental model isn't confirmed yields `isGated = true`, which the UI uses
 * to nudge the user toward the next legit step instead of silently letting
 * them push verification commands that the backend would reject anyway.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

export const CHIPIX_MODES = {
  DESIGN: "design",
  VERIFY: "verify",
};

const VALID_MODES = new Set([CHIPIX_MODES.DESIGN, CHIPIX_MODES.VERIFY]);

const STORAGE_PREFIX = "chipverify.chipixMode";
const DEFAULT_MODE = CHIPIX_MODES.DESIGN;

function storageKey(projectId) {
  return projectId ? `${STORAGE_PREFIX}:${projectId}` : null;
}

function readStoredMode(projectId) {
  const key = storageKey(projectId);
  if (!key || typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    return VALID_MODES.has(raw) ? raw : null;
  } catch {
    return null;
  }
}

function writeStoredMode(projectId, mode) {
  const key = storageKey(projectId);
  if (!key || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, mode);
  } catch {
    // quota / private mode — silently no-op
  }
}

/**
 * @param {object} opts
 * @param {string} opts.projectId
 * @param {"idle"|"design_complete"|"mm_done"} opts.designLifecycle
 * @param {boolean} opts.hasRuns          — at least one verification run finished
 * @param {boolean} opts.hasFiles         — project has RTL artifacts
 */
export default function useChipixMode({
  projectId,
  designLifecycle = "idle",
  hasRuns = false,
  hasFiles = false,
}) {
  // Cold-start preference, before reading storage. If the project already
  // has runs, the user is most likely thinking about verification; otherwise
  // we honour the doc's "open app → design-first" rule.
  const initialMode = useMemo(() => {
    const stored = readStoredMode(projectId);
    if (stored) return stored;
    if (hasRuns || designLifecycle === "mm_done") return CHIPIX_MODES.VERIFY;
    if (hasFiles) return CHIPIX_MODES.VERIFY;
    return DEFAULT_MODE;
  }, [projectId, hasRuns, designLifecycle, hasFiles]);

  const [mode, setModeState] = useState(initialMode);

  // Re-sync when the user switches project: pull stored value or fall back.
  useEffect(() => {
    const stored = readStoredMode(projectId);
    if (stored) {
      setModeState(stored);
      return;
    }
    setModeState(hasRuns || designLifecycle === "mm_done"
      ? CHIPIX_MODES.VERIFY
      : DEFAULT_MODE);
    // We deliberately do NOT depend on `hasRuns` / `designLifecycle` here.
    // Those change *during* a session and should not silently override the
    // user's manual selection. They only seed the initial mode on project
    // switch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const setMode = useCallback((next) => {
    if (!VALID_MODES.has(next)) return;
    setModeState((prev) => {
      if (prev === next) return prev;
      writeStoredMode(projectId, next);
      return next;
    });
  }, [projectId]);

  const toggleMode = useCallback(() => {
    setMode(mode === CHIPIX_MODES.DESIGN ? CHIPIX_MODES.VERIFY : CHIPIX_MODES.DESIGN);
  }, [mode, setMode]);

  // Derived phase — a short human-readable summary of where the system is,
  // independent of what the user *wants* to look at.
  const derivedPhase = useMemo(() => {
    if (designLifecycle === "mm_done") return "ready-to-verify";
    if (designLifecycle === "design_complete") return "mental-model";
    if (hasFiles) return "mental-model";
    return "empty";
  }, [designLifecycle, hasFiles]);

  // Gating: the user has flipped to verify but the system isn't ready.
  // This does NOT block the toggle — it lets the UI render an inline hint
  // ("Confirm the mental model first") without yanking the lens back.
  const isGated = mode === CHIPIX_MODES.VERIFY
    && (derivedPhase === "empty" || derivedPhase === "mental-model");

  const gateReason = useMemo(() => {
    if (!isGated) return null;
    if (derivedPhase === "empty") return "Attach RTL (and ideally a spec), then we'll build a mental model before verification.";
    if (derivedPhase === "mental-model") {
      return hasFiles && designLifecycle === "idle"
        ? "Build and confirm a mental model from your uploaded files — then verification can start."
        : "One step left — confirm the mental model and we're cleared to verify.";
    }
    return null;
  }, [isGated, derivedPhase, hasFiles, designLifecycle]);

  return {
    mode,
    setMode,
    toggleMode,
    derivedPhase,
    isGated,
    gateReason,
  };
}
