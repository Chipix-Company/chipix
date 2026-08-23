/**
 * Project dashboard — operational metrics for the active RTL project.
 * Distinct from per-run verification evidence (thread RunCard / VerdictCard).
 */

const TERMINAL_PASS = new Set(["completed", "passed"]);
const TERMINAL_FAIL = new Set(["failed", "cancelled", "interrupted", "blocked"]);
const IN_FLIGHT = new Set(["running", "queued"]);

function runId(run) {
  return String(run?.id || run?.run_id || "").trim();
}

function runStatus(run) {
  return String(run?.status || "").toLowerCase();
}

function runProjectId(run) {
  return String(run?.project_id || "").trim();
}

function parseRunTime(run) {
  const raw = run?.completed_at || run?.updated_at || run?.created_at;
  if (!raw) return null;
  const t = new Date(raw).getTime();
  return Number.isFinite(t) ? t : null;
}

/**
 * @param {object} params
 * @param {string} params.projectId
 * @param {string} [params.projectName]
 * @param {{ spec?: object[], rtl?: object[], generated?: object[] }} [params.artifactBuckets]
 * @param {object[]} [params.runs] — all org runs; filtered to project
 * @param {object} [params.orgMetrics] — GET /api/v1/metrics
 * @param {object} [params.tokenUsage] — GET /api/v1/projects/{id}/token-usage
 * @param {string} [params.activeRunId]
 */
export function buildProjectDashboardModel({
  projectId,
  projectName = "Project",
  artifactBuckets = {},
  runs = [],
  orgMetrics = {},
  tokenUsage = null,
  activeRunId = null,
}) {
  const pid = String(projectId || "").trim();
  const projectRuns = pid
    ? runs.filter((r) => runProjectId(r) === pid)
    : [];

  const specArts = artifactBuckets.spec || [];
  const rtlArts = artifactBuckets.rtl || [];
  const genArts = artifactBuckets.generated || [];
  const flat = [...specArts, ...rtlArts, ...genArts];

  const files = {
    spec: specArts.length,
    rtl: rtlArts.length,
    generated: genArts.length,
    total: flat.length,
  };

  let passed = 0;
  let failed = 0;
  let inFlight = 0;
  let other = 0;

  for (const r of projectRuns) {
    const s = runStatus(r);
    if (IN_FLIGHT.has(s)) inFlight += 1;
    else if (TERMINAL_PASS.has(s)) passed += 1;
    else if (TERMINAL_FAIL.has(s)) failed += 1;
    else other += 1;
  }

  const finished = passed + failed;
  const passRate = finished > 0 ? Math.round((passed / finished) * 100) : null;

  const sorted = [...projectRuns].sort((a, b) => {
    const ta = parseRunTime(a) || 0;
    const tb = parseRunTime(b) || 0;
    return tb - ta;
  });

  const recentRuns = sorted.slice(0, 8).map((r) => {
    const s = runStatus(r);
    let tone = "idle";
    if (IN_FLIGHT.has(s)) tone = "busy";
    else if (TERMINAL_PASS.has(s)) tone = "good";
    else if (TERMINAL_FAIL.has(s)) tone = "bad";
    return {
      id: runId(r),
      title: r.title || r.summary || `Run ${runId(r).slice(0, 8)}`,
      status: s,
      tone,
      at: r.completed_at || r.updated_at || r.created_at,
      isActive: activeRunId && runId(r) === String(activeRunId),
    };
  });

  // Last 14 runs for sparkline / bar chart (oldest → newest)
  const chartRuns = [...projectRuns]
    .sort((a, b) => (parseRunTime(a) || 0) - (parseRunTime(b) || 0))
    .slice(-14)
    .map((r) => {
      const s = runStatus(r);
      let outcome = "other";
      if (IN_FLIGHT.has(s)) outcome = "running";
      else if (TERMINAL_PASS.has(s)) outcome = "pass";
      else if (TERMINAL_FAIL.has(s)) outcome = "fail";
      return {
        id: runId(r),
        outcome,
        at: parseRunTime(r),
      };
    });

  // Runs per day (last 7 calendar days) for area-style trend
  const now = Date.now();
  const dayMs = 86400000;
  const trendDays = [];
  for (let i = 6; i >= 0; i -= 1) {
    const dayStart = new Date(now - i * dayMs);
    dayStart.setHours(0, 0, 0, 0);
    const dayEnd = dayStart.getTime() + dayMs;
    const label = dayStart.toLocaleDateString(undefined, { weekday: "short" });
    let dayPass = 0;
    let dayFail = 0;
    for (const r of projectRuns) {
      const t = parseRunTime(r);
      if (t == null || t < dayStart.getTime() || t >= dayEnd) continue;
      const s = runStatus(r);
      if (TERMINAL_PASS.has(s)) dayPass += 1;
      else if (TERMINAL_FAIL.has(s)) dayFail += 1;
    }
    trendDays.push({ label, pass: dayPass, fail: dayFail, total: dayPass + dayFail });
  }

  return {
    projectId: pid,
    projectName,
    files,
    runs: {
      total: projectRuns.length,
      passed,
      failed,
      inFlight,
      other,
      passRate,
    },
    recentRuns,
    chartRuns,
    trendDays,
    org: {
      totalProjects: orgMetrics.total_projects ?? null,
      totalRunsOrg: orgMetrics.total_runs ?? null,
      passRateOrg: orgMetrics.pass_rate ?? null,
      tokenUsage: orgMetrics.token_usage ?? null,
    },
    tokenUsage: tokenUsage?.summary || {
      input_tokens: 0,
      output_tokens: 0,
      total_tokens: 0,
      entries: 0,
      estimated_entries: 0,
      by_provider: {},
      by_model: {},
    },
  };
}
