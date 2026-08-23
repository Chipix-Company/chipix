import { useCallback, useEffect, useState } from "react";
import { getSimulators } from "../../services/edaWorkspaceApi";

/**
 * useCadenceStatus — GET /api/v1/tools/simulators, reduced to a single
 * always-visible Cadence chip descriptor.
 *
 * Returns { loading, status, label, tone, title, detail, refresh }:
 *   tone "ok"   → ready (binary + license)
 *   tone "warn" → found but no license configured
 *   tone "off"  → not installed / Linux-only host
 *   detail      → raw xcelium detection object (path, version, details.*)
 *   refresh()   → re-probe (used after the user edits the manual override)
 */
const STATUS_VIEW = {
  ready: { label: "Cadence", tone: "ok", text: "Cadence Xcelium connected" },
  no_license: { label: "Cadence · no license", tone: "warn", text: "xrun found but no Cadence license configured" },
  not_installed: { label: "Cadence · off", tone: "off", text: "Cadence Xcelium not detected" },
  unsupported_platform: { label: "Cadence · Linux only", tone: "off", text: "Cadence Xcelium requires a Linux host" },
};

export default function useCadenceStatus(authToken, { enabled = true } = {}) {
  const [loading, setLoading] = useState(true);
  const [raw, setRaw] = useState(null);

  const load = useCallback(
    ({ refresh = false } = {}) => {
      if (!enabled) { setLoading(false); return Promise.resolve(null); }
      setLoading(true);
      return getSimulators(authToken, { refresh })
        .then((r) => { setRaw(r || {}); setLoading(false); return r; })
        .catch(() => { setRaw({}); setLoading(false); return null; });
    },
    [authToken, enabled],
  );

  useEffect(() => {
    let cancelled = false;
    load().then((r) => { if (cancelled) return r; return r; });
    return () => { cancelled = true; };
  }, [load]);

  const xcelium = raw?.simulators?.xcelium || null;
  const status = xcelium?.details?.status || (xcelium?.available ? "ready" : "not_installed");
  const view = STATUS_VIEW[status] || STATUS_VIEW.not_installed;
  const version = xcelium?.version ? ` — ${xcelium.version}` : "";
  const guidance = !xcelium?.available && xcelium?.guidance ? ` (${xcelium.guidance})` : "";

  return {
    loading,
    status,
    label: view.label,
    tone: view.tone,
    title: `${view.text}${version}${guidance}`,
    detail: xcelium,
    refresh: () => load({ refresh: true }),
  };
}
