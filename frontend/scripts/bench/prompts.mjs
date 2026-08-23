export const BENCH_PROMPTS = [
  "Summarize why session isolation matters in agent chat systems in 5 bullet points.",
  "Given a chat app with stale errors across sessions, list top 3 root causes and one mitigation each.",
  "Write a concise migration checklist to move from custom tool-calling to a reliable SDK runtime.",
];

export const BENCH_CONFIG = {
  provider: process.env.BENCH_PROVIDER || "google",
  model: process.env.BENCH_MODEL || "gemini-2.0-flash",
  maxRuns: Number(process.env.BENCH_MAX_RUNS || 3),
  maxOutputTokens: Number(process.env.BENCH_MAX_OUTPUT_TOKENS || 220),
  temperature: Number(process.env.BENCH_TEMPERATURE || 0.2),
};

export function percentile(values, p) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[idx];
}

export function summarizeMetrics(name, runs) {
  const latencies = runs.map((r) => r.latencyMs);
  const chars = runs.map((r) => r.outputChars);
  const failures = runs.filter((r) => !r.ok).length;

  return {
    sdk: name,
    totalRuns: runs.length,
    successRuns: runs.length - failures,
    failedRuns: failures,
    avgLatencyMs: Math.round(latencies.reduce((a, b) => a + b, 0) / Math.max(1, latencies.length)),
    p95LatencyMs: Math.round(percentile(latencies, 95)),
    avgOutputChars: Math.round(chars.reduce((a, b) => a + b, 0) / Math.max(1, chars.length)),
    runs,
  };
}

export function printSummary(summary) {
  console.log(`\n=== ${summary.sdk} ===`);
  console.log(`Runs: ${summary.successRuns}/${summary.totalRuns} succeeded`);
  console.log(`Avg latency: ${summary.avgLatencyMs} ms`);
  console.log(`P95 latency: ${summary.p95LatencyMs} ms`);
  console.log(`Avg output chars: ${summary.avgOutputChars}`);
  if (summary.failedRuns > 0) {
    const failed = summary.runs.filter((r) => !r.ok);
    console.log("Failures:");
    for (const row of failed) {
      console.log(`- run ${row.run}: ${row.error}`);
    }
  }
}
