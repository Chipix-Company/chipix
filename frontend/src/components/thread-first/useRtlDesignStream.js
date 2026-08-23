import { useCallback, useEffect, useRef, useState } from "react";
import API_BASE_URL from "../../config";
import { createSimulatorRun } from "../../services/edaWorkspaceApi";
import { parseChipixFences, streamingProse } from "./chipixFences";
import {
  extractInFlightFileWrite,
  liveWriteFromToolArgs,
} from "./liveWritePeek";
import { mergeAssistantTailText } from "./assistantBufferMerge";
import { THREAD_KINDS, makeItem, nextItemId, nextTurnId } from "./types";
import { AnalyticsEvents, captureError, captureMessage, track } from "../../lib/observability";

function normalizeAssistantWs(s) {
  return String(s || "").replace(/\s+/g, " ").trim();
}

/** True when attemptCompletion summary would repeat prose already flushed to a Said card. */
function assistantSummaryDuplicatesFlushedProse(flushedProse, summary) {
  const p = normalizeAssistantWs(flushedProse);
  const t = normalizeAssistantWs(summary);
  if (!t || !p) return false;
  if (p === t) return true;
  const shorter = p.length <= t.length ? p : t;
  const longer = p.length > t.length ? p : t;
  if (shorter.length < 40) return false;
  return longer.includes(shorter);
}

export { normalizeAskPayload, parseChipixFences, streamingProse } from "./chipixFences";

/**
 * Build a websocket URL relative to API_BASE_URL.
 */
function buildWsUrl(path, query = {}) {
  const wsBase = API_BASE_URL.replace(/^http/i, (prefix) =>
    prefix.toLowerCase() === "https" ? "wss" : "ws",
  );
  const normalizedBase = wsBase.endsWith("/") ? wsBase : `${wsBase}/`;
  const normalizedPath = path.startsWith("/") ? path.slice(1) : path;
  const url = new URL(normalizedPath, normalizedBase);
  Object.entries(query || {}).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    url.searchParams.set(k, String(v));
  });
  return url.toString();
}

/**
 * Friendly label for a tool the agent just invoked. Drives the live
 * thinking-row text so the user knows what's happening at every moment.
 * Returns null for tools that shouldn't be surfaced as a visible step.
 */
function labelForToolCall(tool, args = {}) {
  const filename = args?.filename || args?.path || args?.file;
  switch (tool) {
    case "createFile":           return filename ? `Writing ${filename}` : "Writing a file";
    case "readFile":             return filename ? `Reading ${filename}` : "Reading a file";
    case "readFiles":            return "Reading project files";
    case "applyCodeToFile":      return filename ? `Editing ${filename}` : "Editing a file";
    case "smart_insert":
    case "replaceSelection":
    case "replaceFile":          return filename ? `Updating ${filename}` : "Updating a file";
    case "runSimulation":        return "Running simulation";
    case "checkCadenceStatus":   return "Checking Cadence connection";
    case "runCadenceSimulation": return "Running Cadence simulation";
    case "getCadenceRunStatus":  return "Checking simulation progress";
    case "applyCadenceFixes":    return "Applying Cadence fixes";
    case "diagnoseAndFix":       return "Diagnosing simulation failure";
    case "runVerification":      return "Running verification";
    case "queryMentalModel":     return "Reviewing my mental model";
    case "buildMentalModel":     return "Building my mental model";
    case "listFiles":            return null; // too noisy
    case "attemptCompletion":    return "Wrapping up";
    default:
      return null;
  }
}

const CADENCE_STAGE_DEFS = [
  { id: "detect", label: "Detect" },
  { id: "compile", label: "Compile" },
  { id: "elaborate", label: "Elaborate" },
  { id: "simulate", label: "Simulate" },
  { id: "repair", label: "Repair" },
];

function initialCadenceStages() {
  return CADENCE_STAGE_DEFS.map((s, i) => ({
    ...s,
    state: i === 0 ? "now" : "todo",
  }));
}

function advanceCadenceStages(stages, phase, phaseStatus) {
  const order = CADENCE_STAGE_DEFS.map((s) => s.id);
  const idx = order.indexOf(phase);
  if (idx < 0) return stages;
  return (stages || initialCadenceStages()).map((s) => {
    const sIdx = order.indexOf(s.id);
    if (sIdx < idx) return { ...s, state: "done" };
    if (sIdx === idx) {
      if (phaseStatus === "passed" || phaseStatus === "ready" || phase === "complete") {
        return { ...s, state: "done" };
      }
      if (phaseStatus === "failed" || phaseStatus === "unavailable") {
        return { ...s, state: "bad" };
      }
      return { ...s, state: "now" };
    }
    if (sIdx === idx + 1 && (phaseStatus === "passed" || phaseStatus === "ready")) {
      return { ...s, state: "now" };
    }
    return { ...s, state: s.state === "done" || s.state === "bad" ? s.state : "todo" };
  });
}

function cadencePhaseLabel(phase) {
  return CADENCE_STAGE_DEFS.find((s) => s.id === phase)?.label || String(phase || "Working");
}

/**
 * useRtlDesignStream
 *
 * Wraps the `/api/v1/ws/agent/{threadId}/chat` protocol used by the RTL
 * design agent and translates each WS event into thread items for the
 * thread-first workspace.
 *
 * Returns:
 *   send(text, options?)   — opens the stream and pushes one user turn
 *   cancel()               — closes the WS cleanly, finishes thinking, resets
 *   isStreaming            — true while a turn is in flight
 *   currentFile            — filename the agent is currently writing, or null
 *   streamStartedAt        — Date.now() when the current stream began
 */
export default function useRtlDesignStream({
  authToken,
  projectId,
  projectName,
  threadId: controlledThreadId,
  onRefreshArtifacts,
  onBeforeSend,
  onTitleUpdated,
  onDesignPhaseComplete,
  onClarifierSession,
  onImplementationPlan,
  onTokenUsageUpdated,
  onProjectTaskUpdated,
  pushItem,
  replaceItem,
  removeItem,
}) {
  const [isStreaming, setIsStreaming] = useState(false);
  const [currentFile, setCurrentFile] = useState(null);
  const [liveWrite, setLiveWrite] = useState(null);
  const [streamStartedAt, setStreamStartedAt] = useState(null);
  const [fallbackThreadId, setFallbackThreadId] = useState(null);

  const threadOkRef = useRef(false);
  const wsRef = useRef(null);
  const activeListenerRef = useRef(null);
  const thinkingIdRef = useRef(null);
  const streamingSaidIdRef = useRef(null);
  const assistantBufferRef = useRef({ text: "" });
  const cancelledRef = useRef(false);
  // Per-turn metrics for the closing summary card.
  const turnStatsRef = useRef({ startedAt: null, tools: 0, files: 0, errors: 0 });
  const activeTurnIdRef = useRef(null);
  // Per-turn task-list card id so we can mutate it on task_started/_completed.
  const taskListIdRef = useRef(null);
  const cadenceRunIdRef = useRef(null);
  const onClarifierSessionRef = useRef(onClarifierSession);
  const onImplementationPlanRef = useRef(onImplementationPlan);
  const onTokenUsageUpdatedRef = useRef(onTokenUsageUpdated);
  const onProjectTaskUpdatedRef = useRef(onProjectTaskUpdated);
  // createFile cards are batched at turn end so summary prose stays above all files.
  const pendingFilesRef = useRef([]);

  useEffect(() => {
    onClarifierSessionRef.current = onClarifierSession;
    onImplementationPlanRef.current = onImplementationPlan;
    onTokenUsageUpdatedRef.current = onTokenUsageUpdated;
    onProjectTaskUpdatedRef.current = onProjectTaskUpdated;
  }, [onClarifierSession, onImplementationPlan, onTokenUsageUpdated, onProjectTaskUpdated]);

  useEffect(() => {
    return () => {
      try { wsRef.current?.close(); } catch { /* noop */ }
    };
  }, []);

  // Controlled mode: the parent (useChatThreads) owns the active thread id.
  // Fallback mode: legacy auto-create one thread per project.
  const ensureThread = useCallback(async () => {
    if (controlledThreadId) return controlledThreadId;
    if (threadOkRef.current && fallbackThreadId) return fallbackThreadId;
    if (!projectId) return null;
    try {
      const r = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/chat/threads`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
        },
        body: JSON.stringify({ title: "Project Chat" }),
      });
      if (r.ok) {
        const d = await r.json();
        const id = d?.id || d?.thread?.id;
        if (id) {
          threadOkRef.current = true;
          setFallbackThreadId(id);
          return id;
        }
      }
    } catch { /* fall through */ }
    return null;
  }, [authToken, projectId, controlledThreadId, fallbackThreadId]);

  // When the controlled thread id changes, close any open socket so the next
  // send opens a fresh one bound to the new thread. The WS path embeds the
  // thread id; reusing across threads would route events to the wrong place.
  useEffect(() => {
    if (!controlledThreadId) return;
    try { wsRef.current?.close(); } catch { /* noop */ }
    wsRef.current = null;
    threadOkRef.current = false;
  }, [controlledThreadId]);

  const getWs = useCallback((tid) => {
    const ex = wsRef.current;
    if (ex && ex.readyState <= WebSocket.OPEN) return ex;
    const ws = new WebSocket(buildWsUrl(`/api/v1/ws/agent/${tid}/chat`, { token: authToken || "" }));
    wsRef.current = ws;
    return ws;
  }, [authToken]);

  const finishThinking = useCallback(() => {
    if (thinkingIdRef.current) {
      const id = thinkingIdRef.current;
      thinkingIdRef.current = null;
      removeItem?.(id);
    }
  }, [removeItem]);

  const detachListener = useCallback(() => {
    const lst = activeListenerRef.current;
    if (lst && wsRef.current) {
      try { wsRef.current.removeEventListener("message", lst); } catch { /* noop */ }
    }
    activeListenerRef.current = null;
  }, []);

  const resetStreamState = useCallback(() => {
    setIsStreaming(false);
    setCurrentFile(null);
    setLiveWrite(null);
    setStreamStartedAt(null);
    assistantBufferRef.current = { text: "" };
    pendingFilesRef.current = [];
    taskListIdRef.current = null;
  }, []);

  const scheduleUsageRefresh = useCallback((reason = "stream_complete") => {
    if (typeof window === "undefined") {
      onTokenUsageUpdatedRef.current?.({ type: "token_usage_refresh", reason });
      return;
    }
    window.setTimeout(() => {
      onTokenUsageUpdatedRef.current?.({ type: "token_usage_refresh", reason });
    }, 900);
  }, []);

  const mergeLiveWrite = useCallback((next) => {
    if (!next) return;
    setLiveWrite((prev) => {
      if (!prev) return next;
      if (prev.filename && next.filename && prev.filename !== next.filename) return next;
      const longer = (next.content || "").length >= (prev.content || "").length
        ? next.content
        : prev.content;
      return {
        ...prev,
        ...next,
        content: longer || next.content || prev.content || "",
      };
    });
  }, []);

  const ensureCadenceRunCard = useCallback((runId) => {
    if (cadenceRunIdRef.current) return cadenceRunIdRef.current;
    const item = makeItem(THREAD_KINDS.CADENCE_RUN, {
      runId: runId || null,
      title: "Cadence Xcelium simulation",
      status: "running",
      stages: initialCadenceStages(),
      log: [],
      currentPhase: { id: "detect", label: "Detect" },
    }, { turnId: activeTurnIdRef.current || undefined });
    cadenceRunIdRef.current = item.id;
    pushItem?.(item);
    return item.id;
  }, [pushItem]);

  const applyCadenceRunEvent = useCallback((event) => {
    if (!replaceItem) return;
    ensureCadenceRunCard(event?.run_id);
    const cardId = cadenceRunIdRef.current;
    if (!cardId) return;
    const phase = String(event?.phase || "");
    const phaseStatus = String(event?.status || "");
    replaceItem(cardId, (prev) => {
      const payload = prev?.payload || {};
      const log = [...(payload.log || [])];
      if (phase) {
        log.push({
          ts: new Date().toISOString(),
          message: `${cadencePhaseLabel(phase)}: ${phaseStatus || "update"}`,
          level: /fail|error|unavailable/i.test(phaseStatus) ? "error" : "info",
        });
      }
      let status = payload.status || "running";
      if (phase === "complete") {
        status = /pass/i.test(phaseStatus) ? "passed" : "failed";
      }
      return {
        payload: {
          ...payload,
          runId: event?.run_id || payload.runId,
          status,
          stages: advanceCadenceStages(payload.stages, phase, phaseStatus),
          log: log.slice(-80),
          currentPhase: phase
            ? { id: phase, label: cadencePhaseLabel(phase) }
            : payload.currentPhase,
        },
      };
    });
  }, [ensureCadenceRunCard, replaceItem]);

  const flushPendingFiles = useCallback(() => {
    const batch = pendingFilesRef.current;
    pendingFilesRef.current = [];
    if (!batch.length) return;
    const baseMs = Date.now();
    const turnId = activeTurnIdRef.current;
    batch.forEach((payload, index) => {
      pushItem?.(makeItem(THREAD_KINDS.FILE, payload, {
        ts: new Date(baseMs + index).toISOString(),
        turnId: turnId || undefined,
      }));
    });
  }, [pushItem]);

  const flushAssistantBuffer = useCallback(() => {
    const raw = (assistantBufferRef.current.text || "").trim();
    assistantBufferRef.current = { text: "" };
    const liveId = streamingSaidIdRef.current;
    streamingSaidIdRef.current = null;

    if (!raw) {
      // Nothing to flush — but if a streaming card was opened with no content
      // (e.g. tool-call only turn), remove it so we don't leave a ghost.
      if (liveId) removeItem?.(liveId);
      return null;
    }

    const { items, prose } = parseChipixFences(raw);
    /** Prose actually shown in-thread after this flush (for deduping attemptCompletion summary). */
    let emittedProse = null;

    if (liveId) {
      if (prose) {
        emittedProse = prose;
        // Lock the streaming card: drop the caret, replace with clean prose.
        replaceItem?.(liveId, () => ({ payload: { text: prose, streaming: false } }));
      } else {
        // All content was structured — remove the empty streaming card.
        removeItem?.(liveId);
      }
    } else if (prose) {
      emittedProse = prose;
      pushItem?.(makeItem(THREAD_KINDS.SAID, { text: prose }, {
        turnId: activeTurnIdRef.current || undefined,
      }));
    }

    const turnId = activeTurnIdRef.current;
    const rtlClarifiers = [];
    const inlineItems = [];
    for (const it of items) {
      const isGate = Boolean(it.payload?._gate);
      if (it.kind === "ask" && !isGate) {
        rtlClarifiers.push(it.payload);
      } else {
        inlineItems.push(it);
      }
    }

    if (rtlClarifiers.length > 0 && onClarifierSessionRef.current) {
      onClarifierSessionRef.current({
        steps: rtlClarifiers,
        intro: prose || undefined,
        turnId: turnId || undefined,
      });
    } else {
      rtlClarifiers.forEach((payload) => {
        inlineItems.push({ kind: "ask", payload });
      });
    }

    inlineItems.forEach((it) => {
      const item = makeItem(it.kind, it.payload, { turnId: turnId || undefined });
      pushItem?.(item);
      if (it.kind === THREAD_KINDS.PLAN && it.payload?._source === "rtl-design") {
        onImplementationPlanRef.current?.(item.id);
      }
    });

    return emittedProse;
  }, [pushItem, replaceItem, removeItem]);

  const ensureStreamingSaid = useCallback(() => {
    if (streamingSaidIdRef.current) return streamingSaidIdRef.current;
    const id = nextItemId("said-live");
    streamingSaidIdRef.current = id;
    pushItem?.(makeItem(THREAD_KINDS.SAID, { text: "", streaming: true }, {
      id,
      turnId: activeTurnIdRef.current || undefined,
    }));
    return id;
  }, [pushItem]);

  const updateStreamingSaid = useCallback(() => {
    const id = streamingSaidIdRef.current;
    if (!id) return;
    const prose = streamingProse(assistantBufferRef.current.text || "");
    replaceItem?.(id, () => ({ payload: { text: prose, streaming: true } }));
  }, [replaceItem]);

  // Emit a quiet recap when a long/work-heavy turn finishes. Short turns
  // and trivial Q&A skip this — the response IS the summary.
  const notifyDesignPhaseComplete = useCallback(() => {
    const filesWritten = turnStatsRef.current.files;
    if (filesWritten > 0 && onDesignPhaseComplete) {
      onDesignPhaseComplete({ filesWritten });
    }
  }, [onDesignPhaseComplete]);

  const maybeEmitTurnSummary = useCallback((headline = null) => {
    const stats = turnStatsRef.current;
    if (!stats.startedAt) return;
    const duration = Date.now() - stats.startedAt;
    const substantial =
      duration > 15000
      || stats.tools > 3
      || stats.files > 1;
    turnStatsRef.current = { startedAt: null, tools: 0, files: 0, errors: 0 };
    if (!substantial) return;
    pushItem?.(makeItem(THREAD_KINDS.TURN_SUMMARY, {
      headline,
      durationMs: duration,
      toolCalls: stats.tools,
      filesWritten: stats.files,
    }, { turnId: activeTurnIdRef.current || undefined }));
  }, [pushItem]);

  const cancel = useCallback(() => {
    cancelledRef.current = true;
    detachListener();
    try { wsRef.current?.close(); } catch { /* noop */ }
    wsRef.current = null;
    threadOkRef.current = false;
    finishThinking();
    // Lock any in-flight streaming Said so the caret stops blinking.
    const liveId = streamingSaidIdRef.current;
    streamingSaidIdRef.current = null;
    if (liveId) {
      replaceItem?.(liveId, (cur) => ({ payload: { ...cur.payload, streaming: false } }));
    }
    flushPendingFiles();
    resetStreamState();
    pushItem?.(makeItem(THREAD_KINDS.SAID, {
      text: "Cancelled. Anything written so far is kept in your project.",
    }));
  }, [detachListener, finishThinking, flushPendingFiles, resetStreamState, pushItem, replaceItem]);

  const send = useCallback(async (text, options = {}) => {
    if (!text?.trim() || !projectId) return;

    // onBeforeSend lets the workspace auto-title the thread from the first
    // user message before we push the user bubble. AWAIT the result — the
    // backend also has an auto-title pass on first message and we need the
    // frontend PATCH to commit first, otherwise the backend reads "Project
    // Chat" and renames to its LLM-generated title, causing a flicker.
    if (onBeforeSend) {
      try { await onBeforeSend(text); } catch { /* non-fatal */ }
    }

    cancelledRef.current = false;
    activeTurnIdRef.current = nextTurnId();
    const turnId = activeTurnIdRef.current;
    turnStatsRef.current = { startedAt: Date.now(), tools: 0, files: 0, errors: 0 };
    taskListIdRef.current = null;
    cadenceRunIdRef.current = null;
    pushItem?.(makeItem(THREAD_KINDS.USER, { text }, { turnId }));

    const thinkingId = nextItemId("thinking");
    thinkingIdRef.current = thinkingId;
    pushItem?.(makeItem(THREAD_KINDS.THINKING, { text: "Working on it" }, { id: thinkingId, turnId }));

    setIsStreaming(true);
    setStreamStartedAt(Date.now());
    setCurrentFile(null);

    track(AnalyticsEvents.RTL_AGENT_MESSAGE_SENT, {
      project_id: projectId,
      thread_id: options.threadId,
      mode: options.mode || "rtl_designer",
    });

    const tid = await ensureThread();
    if (!tid) {
      finishThinking();
      pushItem?.(makeItem(THREAD_KINDS.SAID, { text: "Could not start a conversation thread. Please retry." }));
      resetStreamState();
      return;
    }
    const ws = getWs(tid);
    const pending = {};
    assistantBufferRef.current = { text: "" };

    const onMsg = (evt) => {
      if (cancelledRef.current) return;
      let d;
      try { d = JSON.parse(evt.data); } catch { return; }

      if (["connected", "turn_start", "turn_end", "continue_prompt"].includes(d.type)) {
        return;
      }

      // Backend may emit an LLM-generated title if the thread title was
      // still default at the moment it processed the first message. Let
      // the workspace know so it can update the threads list / drawer chip
      // without a full refetch.
      if (d.type === "thread_title_generated" && d.title) {
        onTitleUpdated?.(d.title);
        return;
      }

      if (
        (d.type === "project_task_created" || d.type === "project_task_updated")
        && d.task
      ) {
        onProjectTaskUpdatedRef.current?.(d.task);
        return;
      }

      // Task-list lifecycle. The agent declares an ordered list of tasks; we
      // render it as one card per turn that mutates as tasks progress, then
      // auto-collapses when everything is done.
      if (d.type === "cadence_run_event") {
        applyCadenceRunEvent(d);
        return;
      }

      if (d.type === "task_list_created" || d.type === "todos_updated") {
        const raw = Array.isArray(d.tasks)
          ? d.tasks
          : Array.isArray(d.todos)
            ? d.todos
            : [];
        const tasks = raw.map((t, i) => ({
          id: t.id || `t-${i}`,
          label: t.label || t.title || t.content || `Task ${i + 1}`,
          state: t.state || t.status || "todo",
          detail: t.detail || null,
        }));
        if (!tasks.length) return;
        if (!taskListIdRef.current) {
          const item = makeItem(THREAD_KINDS.TASK_LIST, { tasks });
          taskListIdRef.current = item.id;
          pushItem?.(item);
        } else if (replaceItem) {
          replaceItem(taskListIdRef.current, () => ({ payload: { tasks } }));
        }
        return;
      }

      if (d.type === "task_started" || d.type === "task_completed") {
        const id = taskListIdRef.current;
        if (!id || !replaceItem) return;
        const taskId = d.task_id || d.id || d.task?.id;
        if (!taskId) return;
        const newState = d.type === "task_completed" ? "done" : "now";
        replaceItem(id, (prev) => {
          const next = (prev.payload?.tasks || []).map((t) => {
            if (t.id !== taskId) {
              // When one task moves to "now", others currently "now" return to "todo".
              if (newState === "now" && t.state === "now") return { ...t, state: "todo" };
              return t;
            }
            return { ...t, state: newState, detail: d.message || d.detail || t.detail };
          });
          return { payload: { ...prev.payload, tasks: next } };
        });
        return;
      }

      if (d.type === "assistant_delta" || d.type === "text_delta") {
        assistantBufferRef.current.text += d.content || d.text || "";
        const inFlight = extractInFlightFileWrite(assistantBufferRef.current.text);
        if (inFlight?.content || inFlight?.filename) {
          if (inFlight.filename) setCurrentFile(inFlight.filename);
          mergeLiveWrite({
            filename: inFlight.filename || undefined,
            content: inFlight.content || "",
            language: inFlight.language,
            tool: "createFile",
            phase: "streaming",
          });
        }
        // The first prose-bearing delta promotes the global thinking row
        // into a live Said card; subsequent deltas update it in place.
        const liveProse = streamingProse(assistantBufferRef.current.text);
        if (liveProse) {
          ensureStreamingSaid();
          // Once we have a live Said showing prose, the global thinking row
          // is redundant — drop it so the user only sees one in-flight signal.
          finishThinking();
          updateStreamingSaid();
        }
        return;
      }

      if (d.type === "tool_call_started") {
        if (d.call_id) pending[d.call_id] = { tool: d.tool, args: d.args };
        if (d.tool === "runCadenceSimulation") {
          ensureCadenceRunCard(d.args?.run_id);
        }
        // Count visible tool calls (skip noise like listFiles).
        if (labelForToolCall(d.tool, d.args)) turnStatsRef.current.tools += 1;
        const label = labelForToolCall(d.tool, d.args);
        const fromTool = liveWriteFromToolArgs(d.tool, d.args);
        if (fromTool) {
          if (fromTool.filename) setCurrentFile(fromTool.filename);
          mergeLiveWrite(fromTool);
        } else if (d.tool === "createFile" && d.args?.filename) {
          setCurrentFile(d.args.filename);
        }
        if (label && thinkingIdRef.current && replaceItem) {
          replaceItem(thinkingIdRef.current, () => ({
            payload: { text: label },
          }));
        }
        return;
      }

      if (d.type === "tool_call_completed") {
        const rem = pending[d.call_id] || {};
        delete pending[d.call_id];

        if (d.tool === "runCadenceSimulation" || rem.tool === "runCadenceSimulation") {
          const cardId = cadenceRunIdRef.current;
          let parsed = null;
          try {
            parsed = JSON.parse(d.result || d.result_summary || "{}");
          } catch {
            parsed = null;
          }
          if (cardId && replaceItem) {
            const finalStatus = String(parsed?.status || "").toLowerCase();
            replaceItem(cardId, (prev) => ({
              payload: {
                ...(prev?.payload || {}),
                runId: parsed?.run_id || prev?.payload?.runId,
                status: finalStatus || prev?.payload?.status || "failed",
                feedbackMemory: parsed?.feedback_memory || null,
              },
            }));
          }
        }

        if (d.tool === "createFile" || rem.tool === "createFile") {
          const fn = rem.args?.filename
            || (d.result_summary || "").match(/`([^`]+\.(sv|v|vh|vhd))`/i)?.[1];
          const lc = parseInt((d.result_summary || "").match(/\((\d+) lines?\)/i)?.[1] || "0", 10);
          const artifactId = d.result?.artifact?.id || null;
          if (fn) {
            turnStatsRef.current.files += 1;
            pendingFilesRef.current.push({
              name: fn,
              artifactId,
              summary: "Generated by RTL agent",
              addedLines: lc || undefined,
            });
          }
          setCurrentFile(null);
          setLiveWrite((prev) => (
            prev && fn && prev.filename === fn
              ? { ...prev, phase: "done" }
              : prev
          ));
          if (onRefreshArtifacts && projectId) {
            setTimeout(() => onRefreshArtifacts(projectId, { silent: true }), 700);
          }
        }
        // applyCodeToFile mutates an existing artifact's content. The
        // drawer/IDE need to refresh too, otherwise the edited file shows
        // the pre-edit content until the next slow poll.
        else if (d.tool === "applyCodeToFile" || rem.tool === "applyCodeToFile") {
          turnStatsRef.current.files += 1;
          setLiveWrite((prev) => (prev ? { ...prev, phase: "done" } : prev));
          if (onRefreshArtifacts && projectId) {
            setTimeout(() => onRefreshArtifacts(projectId, { silent: true }), 500);
          }
        }

        // For non-createFile tools, gently reset the thinking label so the
        // user doesn't see a stale "Reading X" after the read completes.
        if (
          thinkingIdRef.current
          && replaceItem
          && !streamingSaidIdRef.current
          && d.tool !== "createFile"
          && rem.tool !== "createFile"
          && d.tool !== "attemptCompletion"
          && rem.tool !== "attemptCompletion"
        ) {
          replaceItem(thinkingIdRef.current, () => ({ payload: { text: "Working on it" } }));
        }

        if (d.tool === "attemptCompletion" || rem.tool === "attemptCompletion") {
          const flushedProse = flushAssistantBuffer();
          const summary = String(d.result_summary || rem.args?.summary || "").trim();
          if (summary && !assistantSummaryDuplicatesFlushedProse(flushedProse, summary)) {
            pushItem?.(makeItem(THREAD_KINDS.SAID, { text: summary }));
          }
          flushPendingFiles();
          notifyDesignPhaseComplete();
          finishThinking();
          maybeEmitTurnSummary();
          scheduleUsageRefresh("attempt_completion");
          resetStreamState();
          detachListener();
        }
        return;
      }

      if (d.type === "token_usage_updated") {
        onTokenUsageUpdatedRef.current?.(d);
        return;
      }

      if (["done", "complete", "generation_complete", "conversation_ended", "summary"].includes(d.type)) {
        const tailText = d.content || d.text || d.summary || "";
        if (tailText) {
          assistantBufferRef.current.text = mergeAssistantTailText(
            assistantBufferRef.current.text,
            tailText,
          );
        }
        flushAssistantBuffer();
        flushPendingFiles();
        notifyDesignPhaseComplete();
        finishThinking();
        maybeEmitTurnSummary();
        track(AnalyticsEvents.RTL_AGENT_STREAM_COMPLETED, {
          project_id: projectId,
          thread_id: tid,
          tools: turnStatsRef.current.tools,
          files: turnStatsRef.current.files,
        });
        scheduleUsageRefresh("stream_done");
        resetStreamState();
        detachListener();
        return;
      }

      if (d.type === "error") {
        finishThinking();
        flushPendingFiles();
        const errorMessage = d.message || d.error || "unknown";
        track(AnalyticsEvents.RTL_AGENT_ERROR, {
          project_id: projectId,
          thread_id: tid,
          code: d.code,
          message: errorMessage,
        });
        captureMessage(`RTL agent error: ${errorMessage}`, "error", {
          project_id: projectId,
          thread_id: tid,
          code: d.code,
        });
        pushItem?.(makeItem(THREAD_KINDS.SAID, {
          text: `Agent error: ${errorMessage}`,
        }));
        resetStreamState();
        detachListener();
      }
    };

    const fire = () => {
      ws.addEventListener("message", onMsg);
      activeListenerRef.current = onMsg;
      // The `context` field is structured metadata — never prepend prose to
      // the message itself. Callers patch in active_file / last_run / etc.
      // via options.context so the agent can pull the right file content on
      // its own side instead of paying the prompt-cache cost every turn.
      const mergedContext = {
        mode: options.mode || "rtl_designer",
        project_id: projectId,
        workspace_name: projectName,
        language: options.language || "systemverilog",
        ...(options.context || {}),
      };
      ws.send(JSON.stringify({
        type: "agentic_chat",
        id: `r_${Date.now()}`,
        message: text,
        context: mergedContext,
      }));
    };

    if (ws.readyState === WebSocket.OPEN) {
      fire();
    } else {
      ws.addEventListener("open", fire, { once: true });
      ws.addEventListener("error", () => {
        if (cancelledRef.current) return;
        finishThinking();
        pushItem?.(makeItem(THREAD_KINDS.SAID, { text: "Connection to the agent failed." }));
        resetStreamState();
      }, { once: true });
    }
  }, [
    projectId,
    projectName,
    ensureThread,
    getWs,
    pushItem,
    finishThinking,
    onRefreshArtifacts,
    flushAssistantBuffer,
    resetStreamState,
    scheduleUsageRefresh,
    detachListener,
    replaceItem,
    ensureStreamingSaid,
    updateStreamingSaid,
    maybeEmitTurnSummary,
    onBeforeSend,
    onTitleUpdated,
    notifyDesignPhaseComplete,
    flushPendingFiles,
    mergeLiveWrite,
  ]);

  const runCadenceDirect = useCallback(async ({
    generatedArtifactIds = [],
    topModule = "top_tb",
    uvmTestname = "",
    timeoutSeconds = 300,
  } = {}) => {
    if (!projectId) {
      pushItem?.(makeItem(THREAD_KINDS.SAID, {
        text: "Open a project before running Cadence simulation.",
      }));
      return null;
    }
    cadenceRunIdRef.current = null;
    ensureCadenceRunCard(null);
    pushItem?.(makeItem(THREAD_KINDS.USER, {
      text: "Run Cadence Xcelium simulation on the generated UVM testbench.",
    }));
    pushItem?.(makeItem(THREAD_KINDS.SAID, {
      text: "Starting Cadence Xcelium simulation on your generated UVM collateral…",
    }));
    try {
      const response = await createSimulatorRun(
        projectId,
        {
          generatedArtifactIds,
          topModule,
          uvmTestname,
          timeoutSeconds,
        },
        authToken,
      );
      const run = response?.run || response;
      const runId = run?.run_id || run?.runId || null;
      if (cadenceRunIdRef.current && replaceItem) {
        replaceItem(cadenceRunIdRef.current, (prev) => ({
          payload: {
            ...(prev?.payload || {}),
            runId,
            status: String(run?.status || "running").toLowerCase(),
          },
        }));
      }
      onRefreshArtifacts?.();
      onTokenUsageUpdated?.();
      return response;
    } catch (err) {
      if (cadenceRunIdRef.current && replaceItem) {
        replaceItem(cadenceRunIdRef.current, (prev) => ({
          payload: {
            ...(prev?.payload || {}),
            status: "failed",
            log: [
              ...(prev?.payload?.log || []),
              {
                ts: new Date().toISOString(),
                message: err?.message || "Cadence simulation failed to start.",
                level: "error",
              },
            ],
          },
        }));
      }
      pushItem?.(makeItem(THREAD_KINDS.SAID, {
        text: `Cadence simulation failed to start: ${err?.message || "unknown error"}.`,
      }));
      captureError(err, { feature: "cadence_direct_run", project_id: projectId });
      return null;
    }
  }, [
    authToken,
    ensureCadenceRunCard,
    onRefreshArtifacts,
    onTokenUsageUpdated,
    projectId,
    pushItem,
    replaceItem,
  ]);

  return {
    send,
    cancel,
    runCadenceDirect,
    isStreaming,
    currentFile,
    liveWrite,
    streamStartedAt,
    activeThreadId: controlledThreadId || fallbackThreadId || null,
  };
}
