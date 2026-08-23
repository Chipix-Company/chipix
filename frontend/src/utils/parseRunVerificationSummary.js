/**
 * Parse `Run.verification_summary` from the API into the dashboard object shape
 * produced by `services.verification.dashboard_aggregator` (kpis, scenarios,
 * failures, coverage.bars, overall_status, …).
 *
 * The backend often stores JSON as a string; it may also be a plain sentence.
 */

export function parseRunVerificationDashboard(summary) {
  if (summary == null) return null;
  if (typeof summary === "object" && !Array.isArray(summary)) {
    if (summary.kpis || summary.scenarios || summary.overall_status || summary.failures) {
      return summary;
    }
    if (summary.dashboard && typeof summary.dashboard === "object") {
      return summary.dashboard;
    }
    return null;
  }
  if (typeof summary !== "string") return null;
  const t = summary.trim();
  if (!t.startsWith("{") && !t.startsWith("[")) return null;
  try {
    const o = JSON.parse(t);
    if (!o || typeof o !== "object") return null;
    if (o.dashboard && typeof o.dashboard === "object") return o.dashboard;
    if (o.kpis || o.scenarios || o.overall_status || o.failures || o.coverage) return o;
  } catch {
    return null;
  }
  return null;
}
