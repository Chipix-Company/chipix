import { parseRunVerificationDashboard } from "../../utils/parseRunVerificationSummary";

/** Dashboard payload attached to a run status snapshot (API or parsed summary). */
export function getRunDashboard(activeRunStatus) {
  if (!activeRunStatus) return null;
  const parsed = parseRunVerificationDashboard(activeRunStatus.verification_summary);
  return activeRunStatus.dashboard || parsed || null;
}

/** True when we have scenario/strategy/coverage numbers worth showing — not a hollow "completed". */
export function hasMeasurableVerificationResults(metrics) {
  if (!metrics || metrics.no_detailed_results) return false;
  const kpis = metrics.kpis || {};
  if (Number(kpis.total) > 0) return true;
  if (Array.isArray(metrics.scenarios) && metrics.scenarios.length > 0) return true;
  if (Array.isArray(metrics.failures) && metrics.failures.length > 0) return true;
  const types = metrics.types_summary || metrics.verification_types;
  if (!types) return false;
  const list = Array.isArray(types) ? types : Object.values(types);
  return list.some((t) => {
    if (!t || typeof t !== "object") return false;
    return Number(t.total) > 0 || Number(t.passed) > 0 || Number(t.failed) > 0;
  });
}

export function hasVerificationPhaseEvidence(activeRunStatus) {
  if (!activeRunStatus) return false;
  let summary = activeRunStatus.verification_summary;
  if (typeof summary === "string") {
    try {
      summary = JSON.parse(summary);
    } catch {
      return false;
    }
  }
  return Boolean(summary?.phases && typeof summary.phases === "object" && Object.keys(summary.phases).length > 0);
}

/** Strategies for inline run / verdict chips (UnitSim, Formal, UVM, …). */
export function extractVerificationStrategies(activeRunStatus) {
  const dash = getRunDashboard(activeRunStatus);
  const stratSrc =
    dash?.types_summary
    || dash?.verification_types
    || activeRunStatus?.results
    || null;
  if (!stratSrc) return [];
  const list = Array.isArray(stratSrc)
    ? stratSrc.map((t) => ({ id: t.type || t.id || t.name, ...t }))
    : Object.entries(stratSrc).map(([k, v]) => ({
      id: k,
      ...(typeof v === "object" && v ? v : { status: v }),
    }));
  return list
    .filter((t) => t && t.id)
    .map((t) => ({
      id: t.id,
      name: t.label || t.name || (
        t.id === "unitsim" ? "Unit simulation"
          : t.id === "formal" ? "Formal"
            : t.id === "uvm" ? "UVM"
              : t.id
      ),
      status: String(t.status || "idle").toLowerCase(),
      passed: t.passed ?? t.passed_scenarios ?? null,
      total: t.total ?? t.total_scenarios ?? null,
      detail: t.detail || t.summary || null,
    }));
}

/**
 * Metrics for DashboardOverlay + thread cards. Never fabricates a passing
 * verdict when agents never reported scenarios or strategies.
 */
export function mergeThreadRunMetrics(activeRunStatus) {
  if (!activeRunStatus) return null;
  const dash = getRunDashboard(activeRunStatus);
  const status = String(activeRunStatus.status || "").toLowerCase();
  const isRunning = status === "running" || status === "queued";
  const isFailed = status === "failed" || status === "interrupted" || status === "cancelled";

  if (dash && hasMeasurableVerificationResults(dash)) {
    return { ...dash, no_detailed_results: false };
  }

  if (isRunning) {
    if (dash) {
      return {
        ...dash,
        overall_status: "running",
        no_detailed_results: !hasMeasurableVerificationResults(dash),
      };
    }
    return {
      overall_status: "running",
      no_detailed_results: true,
      run_id: activeRunStatus.run_id || activeRunStatus.id,
      kpis: { pending: 1, progress_pct: 0, total: 0, passed: 0, failed: 0 },
      scenarios: [],
      failures: [],
      coverage: { bars: [] },
    };
  }

  if (hasVerificationPhaseEvidence(activeRunStatus)) {
    return {
      overall_status: isFailed ? "fail" : "partial",
      no_detailed_results: true,
      run_id: activeRunStatus.run_id || activeRunStatus.id,
      timestamp: activeRunStatus.completed_at || activeRunStatus.updated_at || activeRunStatus.created_at,
      runtime_seconds: activeRunStatus.runtime_seconds ?? null,
      kpis: { total: 0, passed: 0, failed: isFailed ? 1 : 0, progress_pct: isFailed ? 0 : 100 },
      scenarios: [],
      failures: [],
      coverage: { bars: [] },
    };
  }

  return null;
}

export function runHasVerificationWork(activeRunStatus) {
  if (!activeRunStatus) return false;
  const status = String(activeRunStatus.status || "").toLowerCase();
  if (status === "running" || status === "queued") return true;
  return hasMeasurableVerificationResults(getRunDashboard(activeRunStatus))
    || hasVerificationPhaseEvidence(activeRunStatus);
}
