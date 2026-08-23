/**
 * ThreadItem vocabulary for the thread-first UI.
 *
 * Every backend signal (run WS events, artifact API, RTL design stream,
 * project chat thread messages) is normalized into one of these item types
 * so the conversation renders as a single ordered timeline.
 *
 * Each item:
 *   - id                 stable string id
 *   - kind               one of THREAD_KINDS values
 *   - ts                 ISO timestamp string (used for chronological ordering)
 *   - turnId             optional; groups one user send + agent response(s)
 *   - backendMessageId   optional; chat_messages.id for user rows from the API
 *   - payload            kind-specific data (see comments below)
 *
 * Kinds:
 *   user            { text }                                      // user message
 *   said            { text, who? }                                // plain AI prose
 *   thinking        { text }                                      // ephemeral spinner row
 *   break           { label }                                     // section separator
 *   ask             { question, sub?, choices: [{id,label}], chosenId?, locked? }
 *   plan            { title, sub?, steps: [{id,label,state,why?}], actions?: [{id,label,kind?}], superseded?: boolean, refineLoading?: boolean }
 *   stagedPrep      { title?, summary?, ready?: boolean, loading?: boolean, error?: string }
 *   stagedRec       { title?, sub?, complexity?, strategies: [{id,label,rationale,recommended?,confidence?}], picked? }
 *   file            { artifactId?, memberPath?, name, language?, summary?, addedLines? }
 *   diff            { filename, summary, lines: [{kind:'add'|'del'|'ctx', code, ln?}], status?: 'pending'|'applied'|'rejected', why? }
 *   run             { runId, title, status, stages?: [{id,label,state}], elapsed?, log?: [{ts,level,message}] }
 *   cadenceRun      { status, detection?, phases: [{id,label,state,detail?,command?}], events?: [], runId? }
 *   designBugReport { report, counts, coverage, traceability, findings }
 *   verdict         { ok: bool, title, sub, facts?: [{k,v}], runId?, actions?: [{id,label}] }
 *   why             { title, body, quote?: { loc, code }, openLink?: { name, line }, file?, line?, source? }
 *   strategyResult  { strategies: [{ id, name, status:'pass'|'fail'|'partial'|'running'|'idle', passed?, total?, detail? }], onOpenDashboard? }
 *   waveform        { failureAt?, signals: [{ name, values: [0|1|'x', ...] }], note? }
 *   mentalModel     { summary, modules?: [{name, role, ports?}], inferred?: any }
 *   strategy        { title, sub?, options: [{id,name,description,why?,recommended?,skip?}], selected: Set|Array }
 *   planApproval    { title, sub?, tests: [{id,label,kind,on}] }
 *   toolchain       { items: [{name, ok}] }
 *   coverage        { rows: [{name, pct, kind?: 'low'|'mid'|'good'}] }
 *   schematic       { svg?: string, note?: string }
 */

export const THREAD_KINDS = Object.freeze({
  USER: "user",
  SAID: "said",
  THINKING: "thinking",
  BREAK: "break",
  ASK: "ask",
  PLAN: "plan",
  FILE: "file",
  DIFF: "diff",
  RUN: "run",
  CADENCE_RUN: "cadenceRun",
  DESIGN_BUG_REPORT: "designBugReport",
  VERDICT: "verdict",
  WHY: "why",
  STRATEGY_RESULT: "strategyResult",
  WAVEFORM: "waveform",
  MENTAL_MODEL: "mentalModel",
  STRATEGY: "strategy",
  PLAN_APPROVAL: "planApproval",
  TOOLCHAIN: "toolchain",
  COVERAGE: "coverage",
  SCHEMATIC: "schematic",
  STAGED_PREP: "stagedPrep",
  STAGED_REC: "stagedRec",
  AGENT_BEAT: "agentBeat",
  TURN_SUMMARY: "turnSummary",
  TASK_LIST: "taskList",
  CODEBASE_GRAPH: "codebaseGraph",
  CADENCE_RUN: "cadenceRun",
});

let counter = 0;
export function nextItemId(prefix = "i") {
  counter += 1;
  return `${prefix}-${Date.now().toString(36)}-${counter.toString(36)}`;
}

/** Monotonic turn id for rewind boundaries (one user send → agent work). */
export function nextTurnId() {
  counter += 1;
  return `turn-${Date.now().toString(36)}-${counter.toString(36)}`;
}

export function makeItem(kind, payload, options = {}) {
  const item = {
    id: options.id || nextItemId(kind),
    kind,
    ts: options.ts || new Date().toISOString(),
    payload: payload || {},
  };
  if (options.turnId) item.turnId = options.turnId;
  if (options.backendMessageId) item.backendMessageId = options.backendMessageId;
  return item;
}
