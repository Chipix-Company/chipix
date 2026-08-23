import { useEffect, useState } from "react";
import { getToolchainStatus } from "../../services/edaWorkspaceApi";

const TOOLS = [
  { key: "yosys",     label: "Yosys",     impact: "schematic preview will be unavailable" },
  { key: "verilator", label: "Verilator", impact: null, fallback: "icarus" },
  { key: "icarus",    label: "Icarus",    impact: null },
  { key: "verible",   label: "Verible",   impact: "advanced linting will be skipped" },
  { key: "xcelium",   label: "Cadence Xcelium", impact: "UVM simulation runs require a Linux host with Cadence + a license" },
];

const DISMISS_KEY = "chipverify.thread-first.toolchain-dismissed";

/**
 * useToolchainCheck — one-shot GET /api/v1/eda/toolchain/status.
 *
 * Returns { loading, missing: [{label, impact?}], dismissed, dismiss }.
 * `missing` enumerates only tools the user should be told about — items
 * where a sensible fallback exists (Verilator → Icarus) are suppressed
 * when the fallback is present.
 */
export default function useToolchainCheck(authToken, { enabled = true } = {}) {
  const [loading, setLoading] = useState(true);
  const [raw, setRaw] = useState(null);
  const [dismissed, setDismissed] = useState(() => {
    if (typeof window === "undefined") return false;
    try { return window.localStorage.getItem(DISMISS_KEY) === "1"; } catch { return false; }
  });

  useEffect(() => {
    if (!enabled) { setLoading(false); return undefined; }
    let cancelled = false;
    getToolchainStatus(authToken)
      .then((r) => { if (!cancelled) { setRaw(r || {}); setLoading(false); } })
      .catch(() => { if (!cancelled) { setRaw({}); setLoading(false); } });
    return () => { cancelled = true; };
  }, [authToken, enabled]);

  const missing = (() => {
    if (!raw) return [];
    const out = [];
    const has = (k) => Boolean(raw?.[k]?.available);
    TOOLS.forEach((t) => {
      if (has(t.key)) return;
      // Suppress Verilator-missing if Icarus is available — Icarus is the fallback.
      if (t.fallback && has(t.fallback)) {
        out.push({
          label: t.label,
          impact: `I'll fall back to ${TOOLS.find((x) => x.key === t.fallback)?.label || t.fallback} for simulation`,
        });
        return;
      }
      out.push({ label: t.label, impact: t.impact || "some features may be unavailable" });
    });
    return out;
  })();

  const dismiss = () => {
    setDismissed(true);
    try { window.localStorage.setItem(DISMISS_KEY, "1"); } catch { /* noop */ }
  };

  return { loading, missing, dismissed, dismiss };
}
