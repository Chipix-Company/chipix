import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

import ChipixTitleBar from "./surfaces/ChipixTitleBar";
import ThreadComposer from "./ThreadComposer";
import ThreadItemRenderer from "./cards";
import CommandPalette from "./surfaces/CommandPalette";
import ProjectDrawer from "./surfaces/ProjectDrawer";
import SingleFileEditor from "./surfaces/SingleFileEditor";
import IdeWorkspace from "./surfaces/IdeWorkspace";
import { getEditorFileKind, inferMonacoLanguage } from "./surfaces/editor/ideEditorUtils";
import { createCompletionDebug } from "./surfaces/editor/ideInlineCompletion/CompletionDebug";
import { formatEditorContent } from "./surfaces/editor/formatEditorContent";
import MentalModelOverlay from "./surfaces/MentalModelOverlay";
import CodebaseGraphOverlay from "./surfaces/CodebaseGraphOverlay";
import DashboardOverlay from "./surfaces/DashboardOverlay";
import TaskBoardOverlay from "./surfaces/TaskBoardOverlay";
import DocumentationOverlay from "./surfaces/DocumentationOverlay";
import FoundersOverlay from "./surfaces/FoundersOverlay";
import ClarifierOverlay from "./surfaces/ClarifierOverlay";
import WorkspaceAttachModal, { MAX_ATTACH, attachmentRowKey } from "./surfaces/WorkspaceAttachModal";
import {
  deleteProjectArtifact,
  renameProjectArtifact,
  setActiveProjectArtifacts,
  updateProjectArtifactContent,
} from "../../api/projectArtifactsApi";
import { queryMentalModel, uploadUvmDebugLogs } from "../../api/verificationApi";
import { fetchCodebaseGraphStatus } from "../../api/codebaseGraphApi";

import useProjectThreadState from "./useProjectThreadState";
import useRtlDesignStream from "./useRtlDesignStream";
import useClarifierSession from "./useClarifierSession";
import usePatchInbox, { patchToDiffPayload } from "./usePatchInbox";
import useStagedVerification from "./useStagedVerification";
import useToolchainCheck from "./useToolchainCheck";
import useCadenceStatus from "./useCadenceStatus";
import useChatThreads from "./useChatThreads";
import useIdeCompanionThread from "./useIdeCompanionThread";
import useThreadRewind from "./useThreadRewind";
import useChipixMode, { CHIPIX_MODES } from "./useChipixMode";
import {
  ARTIFACT_KIND,
  artifactDisplayName,
  artifactKindFromRecord,
  buildProjectArtifactManifest,
  expandProjectArtifactsForWorkspace,
  workspaceFilesFromManifest,
  kindLabel,
  makeUploadJobId,
  uploadLabelForFiles,
} from "./artifactAttach";
import useWorkspaceNavigation from "./useWorkspaceNavigation";
import useProjectTasks from "./useProjectTasks";
import ChipixModeToggle from "./surfaces/ChipixModeToggle";
import ChipixStatusBar from "./surfaces/ChipixStatusBar";
import CadenceConfigModal from "./surfaces/CadenceConfigModal";
import ChipixSideRail from "./surfaces/ChipixSideRail";
import { playSwoosh } from "./useChipixSound";
import {
  GATE,
  MENTAL_MODEL_EXPLAINER,
  makeDesignPhaseBreak,
  makeDesignPhaseCompleteSaid,
  makeImportedDesignOnboardingAsk,
  makeImportedDesignOnboardingSaid,
  makeMentalModelCheckpointSaid,
  makePostDesignGateAsk,
  makeVerificationGateAsk,
  makeVerificationUploadAsk,
} from "./designPhaseGate";
import { THREAD_KINDS, makeItem } from "./types";
import {
  buildCadenceVerificationContext,
  cadenceSimulationPending,
  isCadenceRunPrompt,
} from "./cadenceVerificationContext";
import {
  normalizeMentalModelContent,
  makeMentalModelBuiltSaid,
  makeMentalModelCardItem,
  makeMentalModelShowAsk,
} from "./mentalModelThread";
import {
  saveImplementationPlanState,
  clearImplementationPlanState,
} from "./implementationPlan";
import { mergeThreadRunMetrics } from "./verificationRunMetrics";
import { buildProjectDashboardModel } from "./projectDashboardMetrics";
import { AnalyticsEvents, setAnalyticsContext, track } from "../../lib/observability";
import useChipixOnboarding from "../../onboarding/useChipixOnboarding";

const DEFAULT_CONTEXT_BUDGET_TOKENS = 272000;

function estimateTokensFromText(text) {
  return Math.max(0, Math.ceil(String(text || "").length / 4));
}

function tokenTotalFromUsage(usage) {
  if (!usage) return 0;
  return Number(
    usage.total_tokens
      ?? usage.totalTokens
      ?? usage.summary?.total_tokens
      ?? usage.summary?.totalTokens
      ?? 0,
  ) || 0;
}

function buildComposerTokenContext({
  value,
  attachmentChips = [],
  messages = [],
  projectTokenUsage = null,
  budgetTokens = DEFAULT_CONTEXT_BUDGET_TOKENS,
}) {
  const composerTextTokens = estimateTokensFromText(value);
  const attachmentTokens = attachmentChips.reduce(
    (sum, chip) => sum + estimateTokensFromText(chip?.label || chip?.name),
    0,
  );
  const currentInputTokens = composerTextTokens + attachmentTokens;
  const persistedThreadTokens = (messages || []).reduce(
    (sum, message) => sum + tokenTotalFromUsage(message?.token_usage || message?.tokenUsage),
    0,
  );
  const estimatedThreadTokens = persistedThreadTokens || (messages || []).reduce(
    (sum, message) => sum + estimateTokensFromText(message?.content || message?.text),
    0,
  );
  const usedTokens = Math.max(0, estimatedThreadTokens + currentInputTokens);
  const projectTotalTokens = Number(projectTokenUsage?.summary?.total_tokens || 0);
  const isEstimated = !persistedThreadTokens && (messages?.length > 0 || value || attachmentChips.length > 0);
  const breakdown = [
    {
      id: "conversation",
      label: "Conversation",
      tokens: estimatedThreadTokens,
      color: "#6b8fd4",
    },
    {
      id: "composer",
      label: "This message",
      tokens: composerTextTokens,
      color: "#5a9e6f",
    },
    {
      id: "attachments",
      label: "Attachments",
      tokens: attachmentTokens,
      color: "#c9a04a",
    },
  ].filter((row) => row.tokens > 0);

  return {
    usedTokens,
    budgetTokens,
    currentInputTokens,
    projectTotalTokens,
    percent: budgetTokens > 0 ? (usedTokens / budgetTokens) * 100 : 0,
    isEstimated,
    breakdown,
  };
}

/** Serialize verdict-card payload into WS context so Explain/Promote sees real outcomes. */
function buildVerificationTurnSnapshot(payload) {
  if (!payload || typeof payload !== "object") return "";
  try {
    const snap = {
      verification_source: payload.verificationSource || null,
      title: payload.title,
      sub: payload.sub,
      ok: payload.ok,
      facts: payload.facts,
      run_id: payload.runId ?? payload.run_id ?? null,
      execution_brief: payload.executionBrief ?? null,
      pipeline_digest_json: payload._agentRunDigest ?? null,
    };
    const s = JSON.stringify(snap);
    return s.length > 16000 ? s.slice(0, 16000) : s;
  } catch {
    return "";
  }
}

function stringifyVerificationSnapshot(obj) {
  try {
    const s = JSON.stringify(obj);
    return s.length > 16000 ? s.slice(0, 16000) : s;
  } catch {
    return "";
  }
}

function uploadErrorMessage(err) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail).slice(0, 260);
    } catch {
      return "Upload failed.";
    }
  }
  if (err?.response?.status === 413) {
    return "File is too large for the current backend upload limit.";
  }
  if (err?.message && !/^request failed with status code/i.test(err.message)) {
    return err.message;
  }
  if (err?.response?.status) return `Upload failed (${err.response.status}).`;
  return "Upload failed.";
}

function isVerificationStartPrompt(text) {
  const normalized = String(text || "")
    .toLowerCase()
    .replace(/[^a-z0-9_\s-]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!normalized) return false;

  const informational = /\b(why|what|how|explain|tell|show|status|result|results|failed|failure|error|debug|fix)\b/;
  if (informational.test(normalized)) return false;

  const action = /\b(start|run|launch|begin|execute|perform|do|plan|generate|create)\b/;
  const verificationTarget = /\b(verification|verify|staged|uvm|formal|unitsim|unit sim|unit simulation|simulation|testbench|tests?)\b/;
  if (action.test(normalized) && verificationTarget.test(normalized)) return true;

  return /\b(verify|test)\b.*\b(this|it|design|rtl|block|module|project)\b/.test(normalized);
}

/** Snapshot for dashboard overlay + composer shortcuts — merged into WS context like verdict cards. */
function buildDashboardVerificationSnapshot(metrics, activeRunStatus, activeRunId, focusFailure = null) {
  const dash = activeRunStatus?.dashboard || {};
  const failuresRaw = Array.isArray(metrics?.failures)
    ? metrics.failures
    : Array.isArray(dash.failures)
      ? dash.failures
      : [];
  const base = {
    verification_source: "dashboard_overlay",
    run_id: activeRunId ?? metrics?.run_id ?? dash.run_id ?? null,
    overall_status: metrics?.overall_status ?? dash.overall_status ?? activeRunStatus?.status ?? null,
    kpis: metrics?.kpis ?? dash.kpis ?? {},
    failures: failuresRaw.slice(0, 12),
    scenarios_sample: Array.isArray(metrics?.scenarios) ? metrics.scenarios.slice(0, 16) : [],
    module_name: metrics?.module_name || metrics?.target_module || metrics?.project_name || null,
    verification_summary_snippet:
      typeof activeRunStatus?.verification_summary === "string"
        ? activeRunStatus.verification_summary.slice(0, 6000)
        : null,
  };
  if (focusFailure && typeof focusFailure === "object") {
    base.focus_failure = focusFailure;
  }
  return stringifyVerificationSnapshot(base);
}

/** Recent chat rows for IDE / WS context so the agent knows which thread is active. */
/** Strip merged context / fences so IDE assistant panel stays readable. */
function stripIdeChatDisplay(text) {
  let t = String(text || "").trim();
  if (!t) return "";
  const marker = "\n\n---\nUser message:\n";
  const mIdx = t.indexOf(marker);
  if (mIdx >= 0) t = t.slice(mIdx + marker.length).trim();
  t = t.replace(/```chipix:[^\n]*\n[\s\S]*?```/g, "").trim();
  t = t.replace(/\n{3,}/g, "\n\n");
  if (t.length > 2400) t = `${t.slice(0, 2397)}…`;
  return t;
}

function buildThreadRecap(chatMessages, maxItems = 12) {
  const rows = Array.isArray(chatMessages) ? chatMessages : [];
  const out = rows
    .filter((m) => m && (m.content || m.text))
    .filter((m) => String(m.role || m.author || "").toLowerCase() !== "tool")
    .slice(-maxItems)
    .map((m) => {
      const role = String(m.role || m.author || "assistant").toLowerCase();
      return {
        role: role === "user" ? "user" : "assistant",
        text: String(m.content || m.text || "").trim().slice(0, 700),
      };
    });
  if (!out.length) return "";
  try {
    const s = JSON.stringify(out);
    return s.length > 10000 ? s.slice(0, 10000) : s;
  } catch {
    return "";
  }
}

export const DESIGN_HDL_LANGUAGES = [
  { id: "systemverilog", label: "SystemVerilog", fileExt: ".sv" },
  { id: "verilog", label: "Verilog", fileExt: ".v" },
];

const DEFAULT_DESIGN_HDL = "systemverilog";

function designHdlStorageKey(projectId) {
  return projectId ? `chipverify.designHdlLanguage:${projectId}` : null;
}

function loadDesignHdlLanguage(projectId) {
  const key = designHdlStorageKey(projectId);
  if (!key || typeof window === "undefined") return DEFAULT_DESIGN_HDL;
  try {
    const stored = window.localStorage.getItem(key);
    return stored === "verilog" ? "verilog" : DEFAULT_DESIGN_HDL;
  } catch {
    return DEFAULT_DESIGN_HDL;
  }
}

// Greenfield starters — shown when a project has no files and no run history.
const GREENFIELD_STARTERS = [
  {
    id: "uart",
    lbl: "RTL · Peripheral",
    txt: "UART controller with APB interface",
    sub: "Design + verification end-to-end",
    seed: "Design a UART transceiver with an APB slave interface, configurable baud rate, and 16-deep TX/RX FIFOs.",
  },
  {
    id: "fifo",
    lbl: "RTL · Building block",
    txt: "Synchronous FIFO",
    sub: "Parameterizable depth and width",
    seed: "Design a synchronous FIFO with parameterizable depth and width, full/empty/almost-full flags.",
  },
  {
    id: "alu",
    lbl: "RTL · Datapath",
    txt: "RISC-V style ALU",
    sub: "Add, sub, shift, logic — single cycle",
    seed: "Design a single-cycle RISC-V style ALU supporting add, sub, shift and logical operations with a 4-bit op selector.",
  },
  {
    id: "import",
    lbl: "Existing project",
    txt: "Verify code I already have",
    sub: "Attach RTL and a spec",
    seed: "I want to verify an existing RTL design. I will attach the files and the specification.",
  },
];

// Has-files starters — shown when files exist but no verification has run.
// Each action has an explicit `action` field instead of a chat seed for the
// shortcuts ("verify", "mental-model") that have first-class buttons in
// the rest of the app.
const HAS_FILES_STARTERS = [
  {
    id: "verify-now",
    lbl: "Next step",
    txt: "Verify what you have",
    sub: "Run the full verification pipeline on the active design",
    action: "verify",
  },
  {
    id: "mental-model",
    lbl: "Confidence check",
    txt: "Show me how you understand this",
    sub: "Mental model + hierarchy graph before any changes",
    action: "mental-model",
  },
  {
    id: "extend",
    lbl: "Iterate",
    txt: "Add a feature",
    sub: "Describe what you want and I'll propose a diff",
    seed: "I'd like to extend this design. ",
  },
  {
    id: "fresh",
    lbl: "Start over",
    txt: "Design something different",
    sub: "Keep the project, replace the design",
    seed: "Replace the current design with something new — I'll describe it next.",
  },
];

// Verify-mode starters — shown when the user has flipped the lens to verify.
// These bias toward "what can we check?" rather than "what should we build?".
const VERIFY_STARTERS_EMPTY = [
  {
    id: "import",
    lbl: "Bring your RTL",
    txt: "Verify code I already have",
    sub: "Attach a design + spec, I'll plan and run the checks",
    seed: "I want to verify an existing RTL design. I will attach the files and the specification.",
  },
  {
    id: "uart",
    lbl: "Start from a template",
    txt: "UART with a verification harness",
    sub: "Design + UVM together — fastest end-to-end demo",
    seed: "Design a UART transceiver with an APB slave interface, then build the UVM environment to verify it.",
  },
  {
    id: "fifo",
    lbl: "Start from a template",
    txt: "FIFO with full verification",
    sub: "Parameterizable, with directed + random tests",
    seed: "Design a synchronous FIFO and verify it with directed and random stimulus.",
  },
  {
    id: "design-instead",
    lbl: "Wrong lens?",
    txt: "Switch to design mode",
    sub: "There's nothing built to verify yet",
    action: "switch-to-design",
  },
];

const VERIFY_STARTERS_HAS_FILES = [
  {
    id: "build-mm",
    lbl: "Step 1",
    txt: "Build mental model from my files",
    sub: "Reads spec + RTL — required before verification runs",
    action: "import-verify",
  },
  {
    id: "verify-now",
    lbl: "Run it",
    txt: "Plan and verify",
    sub: "Mental model → strategy → plan → run",
    action: "verify",
  },
  {
    id: "mental-model",
    lbl: "Review",
    txt: "Open mental model",
    sub: "Hierarchy graph + plain-English summary",
    action: "mental-model",
  },
  {
    id: "dashboard",
    lbl: "Catch up",
    txt: "Open last run dashboard",
    sub: "KPIs, failures, coverage at a glance",
    action: "dashboard",
  },
];

// Iterating starters — shown when at least one run exists.
const ITERATING_STARTERS = [
  {
    id: "continue",
    lbl: "Pick up",
    txt: "Continue iterating",
    sub: "Tell me what to improve next",
    seed: "Let's keep iterating. ",
  },
  {
    id: "verify-again",
    lbl: "Re-check",
    txt: "Re-run verification",
    sub: "Same plan, fresh run",
    action: "verify",
  },
  {
    id: "dashboard",
    lbl: "Overview",
    txt: "Project dashboard",
    sub: "Runs, RTL files, pass rate, trends",
    action: "dashboard",
  },
  {
    id: "fresh",
    lbl: "Start over",
    txt: "Start a new design",
    sub: "Begin a fresh conversation in this project",
    action: "reset",
  },
];

const FINAL_STATUSES = new Set(["completed", "passed", "failed", "cancelled", "interrupted"]);

function secondsFromRun(value) {
  if (value == null) return null;
  if (typeof value === "number") return value;
  const match = String(value).match(/(\d+(?:\.\d+)?)/);
  return match ? Number(match[1]) : null;
}

function buildPaletteSections({ projects, files, runs, actions }) {
  const sections = [];
  if (actions?.length) {
    sections.push({ label: "Actions", glyph: "⚡", items: actions });
  }
  if (files?.length) {
    sections.push({
      label: "Files",
      glyph: "F",
      items: files.slice(0, 30).map((a) => ({
        id: `file:${a.id}`,
        name: a.filename || a.metadata?.relative_path || "file",
        meta: a.artifact_type || "",
        ref: a,
        kind: "file",
      })),
    });
  }
  if (runs?.length) {
    sections.push({
      label: "Recent runs",
      glyph: "R",
      items: runs.slice(0, 10).map((r) => ({
        id: `run:${r.id || r.run_id}`,
        name: r.title || r.summary || `Run ${String(r.id || r.run_id || "").slice(0, 8)}`,
        meta: r.status || "",
        ref: r,
        kind: "run",
      })),
    });
  }
  if (projects?.length > 1) {
    sections.push({
      label: "Projects",
      glyph: "P",
      items: projects.map((p) => ({
        id: `proj:${p.id}`,
        name: p.name || "Untitled project",
        meta: "",
        ref: p,
        kind: "project",
      })),
    });
  }
  return sections;
}

export default function ThreadFirstWorkspace({
  authToken,
  projects = [],
  activeProject,
  onSelectProject,
  onSelectActiveRun,
  onCreateProject,
  artifactState,
  artifactsLoading,
  activeRunId,
  activeRunStatus,
  projectRuns = [],
  projectAgents = [],
  isDarkTheme,
  onToggleTheme,
  backendConnectivity = { status: "ok" },

  // Behaviours
  onStartRun,
  onCancelRun,
  onDownloadRun,
  onDeleteRun,
  onFetchArtifactContent,
  onFetchArtifactBlob,
  onSaveRtlProjectMember,
  onRefreshArtifacts,
  onUploadArtifact,
  dashboardMetrics,
  projectTokenUsage,
  onRefreshProjectTokenUsage,
}) {
  // Multi-thread state — one thread per project is the default; multi is
  // opt-in via palette + drawer. The hook handles list/CRUD + message
  // hydration on switch and persists the active selection per project.
  const chatThreads = useChatThreads({
    projectId: activeProject?.id,
    authToken,
    enabled: Boolean(activeProject?.id),
  });

  // Per-thread run association — which run id was started from which thread.
  // Persisted per project so reloads don't surface another thread's run.
  // Map: { [threadId]: runId }. Anything not in this map is treated as
  // belonging to "nowhere" and won't render in any thread's timeline.
  const runForThreadStorageKey = activeProject?.id
    ? `chipverify.runForThread:${activeProject.id}`
    : null;
  const [runForThread, setRunForThread] = useState({});
  useEffect(() => {
    if (!runForThreadStorageKey) { setRunForThread({}); return; }
    try {
      const raw = window.localStorage.getItem(runForThreadStorageKey);
      setRunForThread(raw ? JSON.parse(raw) : {});
    } catch {
      setRunForThread({});
    }
  }, [runForThreadStorageKey]);
  const recordRunForThread = useCallback((threadId, runId) => {
    if (!threadId || !runId) return;
    setRunForThread((prev) => {
      const next = { ...prev, [threadId]: runId };
      if (runForThreadStorageKey) {
        try { window.localStorage.setItem(runForThreadStorageKey, JSON.stringify(next)); } catch { /* quota / private mode */ }
      }
      return next;
    });
  }, [runForThreadStorageKey]);
  const runBelongsToThread = Boolean(
    activeRunId
    && chatThreads.activeThreadId
    && runForThread[chatThreads.activeThreadId] === activeRunId,
  );

  // Thread state — typed timeline. Receives historical chat messages from
  // the active thread so a reload or thread switch shows the prior context.
  const {
    items: derivedItems,
    hasThreadActivity,
    pushItem,
    replaceItem,
    removeItem,
    clearItems,
    setExtras,
    extras,
  } = useProjectThreadState({
    activeProject,
    projectId: activeProject?.id,
    activeThreadId: chatThreads.activeThreadId,
    artifactState,
    activeRunId,
    activeRunStatus,
    projectAgents,
    chatMessages: chatThreads.chatMessages,
    runBelongsToThread,
  });

  const mergedDashboardMetrics = useMemo(
    () => mergeThreadRunMetrics(activeRunStatus) || {},
    [activeRunStatus],
  );

  const nav = useWorkspaceNavigation();
  const {
    dashOpen,
    tasksOpen,
    mmOpen,
    cgOpen,
    ideOpen,
    docsOpen,
    foundersOpen,
    editorTarget,
    openPrimary,
    togglePrimary,
    openFile,
    closeFile,
    goBack,
    closeAll,
    titleBarOverlay,
    sideRailActive,
  } = nav;

  const [codebaseGraphStatus, setCodebaseGraphStatus] = useState(null);
  const codebaseGraphReadyRef = useRef(false);

  useEffect(() => {
    if (!activeProject?.id) {
      setCodebaseGraphStatus(null);
      codebaseGraphReadyRef.current = false;
      return undefined;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const st = await fetchCodebaseGraphStatus(activeProject.id, authToken);
        if (cancelled) return;
        if (st.status === "ready" && !codebaseGraphReadyRef.current) {
          codebaseGraphReadyRef.current = true;
          pushItem(
            makeItem(THREAD_KINDS.CODEBASE_GRAPH, {
              summary: (st.report_excerpt || "").split("\n").slice(0, 2).join(" "),
              nodeCount: st.node_count,
              edgeCount: st.edge_count,
            }),
          );
        }
        if (st.status !== "ready") {
          codebaseGraphReadyRef.current = false;
        }
        setCodebaseGraphStatus(st);
      } catch {
        /* ignore poll errors */
      }
    };
    void poll();
    const intervalMs = codebaseGraphStatus?.status === "building" ? 3000 : 15000;
    const id = setInterval(poll, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [activeProject?.id, authToken, codebaseGraphStatus?.status, pushItem]);

  const projectTasks = useProjectTasks({
    projectId: activeProject?.id,
    authToken,
    enabled: Boolean(activeProject?.id),
    pollWhenOpen: tasksOpen,
  });

  const {
    tasks: projectTaskList,
    loading: projectTasksLoading,
    syncing: projectTasksSyncing,
    error: projectTasksError,
    agents: projectTaskAgents,
    createTask: createProjectTaskOnBoard,
    moveTask: moveProjectTask,
    syncFromThreads: syncProjectTasksFromThreads,
    upsertLocal: upsertProjectTask,
    refresh: refreshProjectTasks,
  } = projectTasks;

  useEffect(() => {
    if (!tasksOpen) return;
    void chatThreads.refreshThreads?.();
  }, [tasksOpen, chatThreads.refreshThreads]);

  const handleRetryProjectTasks = useCallback(async () => {
    await syncProjectTasksFromThreads({ silent: false });
    await refreshProjectTasks({ silent: false });
    await chatThreads.refreshThreads?.();
  }, [syncProjectTasksFromThreads, refreshProjectTasks, chatThreads.refreshThreads]);

  const openProjectDashboard = useCallback(() => {
    openPrimary("dashboard");
  }, [openPrimary]);

  const openTaskBoard = useCallback(() => {
    openPrimary("tasks");
  }, [openPrimary]);

  const stripStagedTimelineFromExtras = useCallback(() => {
    setExtras((prev) => (prev || []).filter((e) => {
      if (!e) return false;
      if (e.kind === THREAD_KINDS.STAGED_PREP || e.kind === THREAD_KINDS.STAGED_REC) return false;
      if (e.kind === THREAD_KINDS.CADENCE_RUN) return false;
      if (e.kind === THREAD_KINDS.DESIGN_BUG_REPORT) return false;
      if (e.kind === THREAD_KINDS.VERDICT && e.payload?.verificationSource === "staged_verification") {
        return false;
      }
      if (e.kind === THREAD_KINDS.MENTAL_MODEL && e.payload?._source === "staged") return false;
      if (e.kind === THREAD_KINDS.SAID && String(e.payload?.text || "").startsWith("Resumed verification plan from")) {
        return false;
      }
      return true;
    }));
  }, [setExtras]);

  const [hasInteracted, setHasInteracted] = useState(false);
  // When the user explicitly asks for a new design from an iterating project,
  // override the project-state-based starter set and show greenfield options
  // (UART/FIFO/ALU/etc.) until the user either picks one or types a message.
  const [forceGreenfieldStarters, setForceGreenfieldStarters] = useState(false);

  // Design → mental model → verification gate (per thread).
  const designGateRef = useRef({
    designComplete: false,
    mentalModelDone: false,
    verifyAskShown: false,
    verifyUploadAskShown: false,
    importedOnboardingShown: false,
  });
  const planGateRef = useRef({
    requirePlanApproval: false,
    activePlanItemId: null,
  });
  const [designLifecycle, setDesignLifecycle] = useState("idle"); // idle | design_complete | mm_done
  const [projectHasMentalModel, setProjectHasMentalModel] = useState(false);
  const onDesignPhaseCompleteRef = useRef(null);
  const clarifier = useClarifierSession();

  // UI state — declare before effects / IDE hooks that reference these setters.
  const [composer, setComposer] = useState("");
  const [composerAttachments, setComposerAttachments] = useState([]);
  const [ideAttachments, setIdeAttachments] = useState([]);
  const [workspaceAttachOpen, setWorkspaceAttachOpen] = useState(false);
  const [ideAttachModalOpen, setIdeAttachModalOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const threadScrollRef = useRef(null);
  const fileInputRef = useRef(null);
  const specInputRef = useRef(null);
  const folderInputRef = useRef(null);
  const uvmLogInputRef = useRef(null);
  const uvmLogContextRef = useRef(null);
  const pendingAttachKindRef = useRef(null);
  const [pendingAttachKind, setPendingAttachKind] = useState(null);
  const [uploadJobs, setUploadJobs] = useState([]);
  const [composerAttachHint, setComposerAttachHint] = useState("");
  const [designHdlLanguage, setDesignHdlLanguage] = useState(
    () => loadDesignHdlLanguage(activeProject?.id),
  );

  useEffect(() => {
    setDesignHdlLanguage(loadDesignHdlLanguage(activeProject?.id));
  }, [activeProject?.id]);

  const setDesignHdlLanguagePersisted = useCallback((next) => {
    const lang = next === "verilog" ? "verilog" : DEFAULT_DESIGN_HDL;
    setDesignHdlLanguage(lang);
    const key = designHdlStorageKey(activeProject?.id);
    if (!key) return;
    try {
      window.localStorage.setItem(key, lang);
    } catch {
      // localStorage is best-effort.
    }
  }, [activeProject?.id]);

  // Every run-start request gets the active thread stamped on the resolved
  // run id, so the Run/Verdict/AgentBeat cards only surface in the thread
  // that actually asked for the run. Caller's onStartRun returns the run id.
  const startRunForActiveThread = useCallback(async (payload) => {
    if (!onStartRun) return null;
    // Precondition: verification needs an RTL artifact. The backend doesn't
    // require one explicitly, so without this guard the run launches and the
    // pipeline fails silently 30s later. Surface the missing-design state up
    // front as a thread item the user can act on.
    const rtlArtifacts = artifactState?.artifacts?.rtl || [];
    if (!rtlArtifacts.length) {
      pushItem(makeItem(THREAD_KINDS.SAID, {
        text: "I don't see any RTL files in this project yet. Tell me what to design first (or attach RTL using the paperclip), and I'll verify it next.",
      }));
      setHasInteracted(true);
      return null;
    }
    if (!designGateRef.current.mentalModelDone) {
      pushItem(makeItem(THREAD_KINDS.SAID, {
        text: `Before verification runs, we need a mental model on your design. ${MENTAL_MODEL_EXPLAINER}`,
      }));
      if (designGateRef.current.designComplete) {
        pushItem(makePostDesignGateAsk());
      } else {
        pushItem(makeImportedDesignOnboardingAsk());
      }
      setHasInteracted(true);
      return null;
    }
    const promptText = (payload?.prompt || "Run verification on the active design.").trim();
    pushItem(makeItem(THREAD_KINDS.USER, { text: promptText }));
    pushItem(makeItem(THREAD_KINDS.SAID, { text: "Queuing verification run…" }));
    setHasInteracted(true);
    const result = await onStartRun(payload);
    const runId = (result && (result.run_id || result.id || result.runId))
      || (typeof result === "string" ? result : null);
    if (runId && chatThreads.activeThreadId) {
      recordRunForThread(chatThreads.activeThreadId, runId);
    }
    return result;
  }, [onStartRun, chatThreads.activeThreadId, recordRunForThread, artifactState, pushItem, setHasInteracted]);

  // When the active chat thread changes, clear local items so we don't show
  // thread A's user/said items inside thread B's hydrated history. Project
  // switches are already handled by useProjectThreadState's own reset.
  // Also reset hasInteracted so the welcome screen re-appears for any empty
  // thread the user switches into.
  const lastThreadIdRef = useRef(chatThreads.activeThreadId);
  useEffect(() => {
    if (lastThreadIdRef.current && lastThreadIdRef.current !== chatThreads.activeThreadId) {
      // Per-thread extras are restored inside useProjectThreadState (localStorage);
      // do not clearItems() here or we'd discard the flush that happens on thread switch.
      setHasInteracted(false);
      setComposer("");
      setComposerAttachments([]);
      setIdeAttachments([]);
      setUploadJobs([]);
      setComposerAttachHint("");
      designGateRef.current = {
        designComplete: false,
        mentalModelDone: false,
        verifyAskShown: false,
        verifyUploadAskShown: false,
        importedOnboardingShown: false,
      };
      setDesignLifecycle("idle");
      // Don't carry the greenfield-override into the *next* thread switch —
      // only the thread the user just asked for should see it.
    }
    lastThreadIdRef.current = chatThreads.activeThreadId;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatThreads.activeThreadId]);

  useEffect(() => {
    designGateRef.current = {
      designComplete: false,
      mentalModelDone: false,
      verifyAskShown: false,
      verifyUploadAskShown: false,
      importedOnboardingShown: false,
    };
    setDesignLifecycle("idle");
    setComposer("");
    setComposerAttachments([]);
    setIdeAttachments([]);
    setUploadJobs([]);
  }, [activeProject?.id]);

  const onImplementationPlanRef = useRef(null);

  const refreshMainTokenUsageViews = useCallback(() => {
    void chatThreads.refreshMessages?.();
    void onRefreshProjectTokenUsage?.();
  }, [chatThreads, onRefreshProjectTokenUsage]);

  // RTL design WS stream → thread items
  const {
    send: sendRtlMessage,
    cancel: cancelRtlStream,
    runCadenceDirect,
    isStreaming: rtlStreaming,
    currentFile: rtlCurrentFile,
    liveWrite: rtlLiveWrite,
    streamStartedAt: rtlStreamStartedAt,
  } = useRtlDesignStream({
    authToken,
    projectId: activeProject?.id,
    projectName: activeProject?.name,
    threadId: chatThreads.activeThreadId,
    // Auto-title before WS send so the backend's first-message rename sees
    // the updated title and skips. Without await the backend would race the
    // PATCH and overwrite with its own LLM-derived title.
    onBeforeSend: (text) => chatThreads.maybeAutoTitle(text),
    onTitleUpdated: (title) => chatThreads.applyLocalTitle(chatThreads.activeThreadId, title),
    onDesignPhaseComplete: (payload) => onDesignPhaseCompleteRef.current?.(payload),
    onClarifierSession: (payload) => {
      track(AnalyticsEvents.CLARIFIER_OPENED, {
        project_id: activeProject?.id,
        question_count: payload?.questions?.length,
      });
      clarifier.open(payload);
    },
    onImplementationPlan: (planItemId) => onImplementationPlanRef.current?.(planItemId),
    onTokenUsageUpdated: refreshMainTokenUsageViews,
    onRefreshArtifacts,
    onProjectTaskUpdated: (task) => {
      if (task) upsertProjectTask(task);
    },
    pushItem,
    replaceItem,
    removeItem,
  });

  const handleOpenBoardTask = useCallback(async (task) => {
    if (!task?.thread_id) return;
    closeAll();
    setDrawerOpen(false);
    await chatThreads.switchThread(task.thread_id);
    setHasInteracted(true);
  }, [chatThreads, closeAll]);

  const handleCreateBoardTask = useCallback(async (payload) => {
    const body = await createProjectTaskOnBoard(payload);
    if (body?.thread?.id) {
      await chatThreads.refreshThreads?.();
      closeAll();
      await chatThreads.switchThread(body.thread.id);
      if (body.thread.title) {
        chatThreads.applyLocalTitle?.(body.thread.id, body.thread.title);
      }
      const prompt = body.initial_prompt || payload.prompt || payload.title;
      if (prompt) {
        setHasInteracted(true);
        await sendRtlMessage(prompt);
      }
    }
    return body;
  }, [chatThreads, closeAll, createProjectTaskOnBoard, sendRtlMessage]);

  const ideCompanion = useIdeCompanionThread({
    projectId: activeProject?.id,
    mainThreadId: chatThreads.activeThreadId,
    authToken,
    enabled: ideOpen,
  });

  const idePushItem = useCallback((item) => {
    if (item?.kind === THREAD_KINDS.FILE && activeProject?.id) {
      onRefreshArtifacts?.(activeProject.id, { silent: true });
    }
  }, [activeProject?.id, onRefreshArtifacts]);

  const refreshIdeTokenUsageViews = useCallback(() => {
    void ideCompanion.refreshIdeMessages?.();
    void onRefreshProjectTokenUsage?.();
  }, [ideCompanion, onRefreshProjectTokenUsage]);

  const {
    send: sendIdeMessage,
    cancel: cancelIdeStream,
    isStreaming: ideStreaming,
  } = useRtlDesignStream({
    authToken,
    projectId: activeProject?.id,
    projectName: activeProject?.name,
    threadId: ideCompanion.ideThreadId,
    onTokenUsageUpdated: refreshIdeTokenUsageViews,
    onRefreshArtifacts,
    pushItem: idePushItem,
    replaceItem: undefined,
    removeItem: undefined,
  });

  const prevIdeStreamingRef = useRef(false);
  useEffect(() => {
    const was = prevIdeStreamingRef.current;
    prevIdeStreamingRef.current = ideStreaming;
    if (was && !ideStreaming) {
      void ideCompanion.refreshIdeMessages();
    }
  }, [ideStreaming, ideCompanion.refreshIdeMessages]);

  const ideComposerTokenContext = useMemo(() => buildComposerTokenContext({
    value: "",
    attachmentChips: ideAttachments,
    messages: ideCompanion.ideMessages,
    projectTokenUsage,
  }), [ideAttachments, ideCompanion.ideMessages, projectTokenUsage]);

  // Patch inbox — polls listPatches and pushes a DiffCard for every new patch.
  // Tracks which patch ids we've already turned into thread items so we don't
  // duplicate when the user keeps the inbox mounted.
  const patchToThreadIdRef = useRef(new Map());
  const handleNewPatch = useCallback((patch) => {
    if (!patch?.id || patchToThreadIdRef.current.has(patch.id)) return;
    const item = makeItem(THREAD_KINDS.DIFF, patchToDiffPayload(patch));
    patchToThreadIdRef.current.set(patch.id, item.id);
    pushItem(item);
  }, [pushItem]);

  const patchInbox = usePatchInbox({
    projectId: activeProject?.id,
    authToken,
    enabled: Boolean(activeProject?.id),
    onNewPatch: handleNewPatch,
  });

  // Staged verification (Prepare → Recommend → Plan → Refine → Execute) as cards.
  const staged = useStagedVerification(activeProject?.id, {
    authToken,
    pushItem,
    replaceItem,
    onStartRun: startRunForActiveThread,
    onCancelRun,
    onBeforeStagedHydrate: stripStagedTimelineFromExtras,
    onArtifactsChanged: () => (
      activeProject?.id
        ? onRefreshArtifacts?.(activeProject.id, { silent: true })
        : null
    ),
  });

  useEffect(() => {
    let cancelled = false;
    if (!activeProject?.id) {
      setProjectHasMentalModel(false);
      return undefined;
    }
    (async () => {
      try {
        await queryMentalModel(activeProject.id, "overview", authToken);
        if (!cancelled) setProjectHasMentalModel(true);
      } catch {
        if (!cancelled) setProjectHasMentalModel(false);
      }
    })();
    return () => { cancelled = true; };
  }, [activeProject?.id, authToken]);

  const openIdeFromArtifacts = useCallback(() => {
    openPrimary("ide");
  }, [openPrimary]);

  useEffect(() => {
    if (!ideOpen || !activeProject?.id) return;
    void onRefreshArtifacts?.(activeProject.id, { silent: true });
  }, [ideOpen, activeProject?.id, onRefreshArtifacts]);

  const pushVerificationGate = useCallback(() => {
    if (designGateRef.current.verifyAskShown) return;
    designGateRef.current.verifyAskShown = true;
    pushItem(makeMentalModelCheckpointSaid());
    pushItem(makeVerificationGateAsk());
    setHasInteracted(true);
  }, [pushItem]);

  const onDesignPhaseComplete = useCallback(({ filesWritten }) => {
    if (!filesWritten || filesWritten < 1) return;
    designGateRef.current.designComplete = true;
    setDesignLifecycle(designGateRef.current.mentalModelDone ? "mm_done" : "design_complete");
    setHasInteracted(true);
    pushItem(makeDesignPhaseBreak());
    pushItem(makeDesignPhaseCompleteSaid(filesWritten));
    if (designGateRef.current.mentalModelDone) {
      pushVerificationGate();
    } else {
      pushItem(makePostDesignGateAsk());
    }
  }, [pushItem, pushVerificationGate]);

  useEffect(() => {
    onDesignPhaseCompleteRef.current = onDesignPhaseComplete;
  }, [onDesignPhaseComplete]);

  // One-shot toolchain probe — only surfaces if something the user might need
  // is missing. Dismissible so it doesn't nag across sessions.
  const toolchain = useToolchainCheck(authToken);
  // Always-visible Cadence Xcelium status chip in the bottom status bar.
  const cadenceStatus = useCadenceStatus(authToken);
  const [cadenceConfigOpen, setCadenceConfigOpen] = useState(false);
  const [graphChatMessages, setGraphChatMessages] = useState([]);

  // When the RTL stream finishes a turn, immediately refresh the patch inbox.
  // Without this the next DiffCard might wait up to 8s for the next poll —
  // long enough to feel like nothing happened after the user asked for a fix.
  const prevRtlStreamingRef = useRef(false);
  useEffect(() => {
    if (prevRtlStreamingRef.current && !rtlStreaming) {
      patchInbox.refresh?.();
    }
    prevRtlStreamingRef.current = rtlStreaming;
  }, [rtlStreaming, patchInbox]);

  const rawFlatArtifacts = useMemo(() => [
    ...(artifactState?.artifacts?.spec || []),
    ...(artifactState?.artifacts?.rtl || []),
    ...(artifactState?.artifacts?.generated || []),
  ], [artifactState]);

  const flatArtifacts = useMemo(
    () => expandProjectArtifactsForWorkspace(rawFlatArtifacts),
    [rawFlatArtifacts],
  );

  const generatedArtifactIdsForDebug = useMemo(() => {
    const ids = [];
    (artifactState?.artifacts?.generated || []).forEach((artifact) => {
      const id = artifact?.artifactId || artifact?.id;
      if (id && !ids.includes(id)) ids.push(id);
    });
    return ids;
  }, [artifactState]);

  const triggerUvmLogUpload = useCallback((payload = {}) => {
    if (!activeProject?.id) {
      pushItem(makeItem(THREAD_KINDS.SAID, {
        text: "Pick a project first, then upload the simulator log.",
      }));
      return;
    }
    uvmLogContextRef.current = {
      runId: payload?.runId || payload?.run_id || null,
      generatedArtifactIds: payload?.generatedArtifactIds?.length
        ? payload.generatedArtifactIds
        : generatedArtifactIdsForDebug,
    };
    uvmLogInputRef.current?.click();
  }, [activeProject?.id, generatedArtifactIdsForDebug, pushItem]);

  const handleUvmDebugLogsPicked = useCallback(async (event) => {
    const list = Array.from(event.target.files || []);
    event.target.value = "";
    if (!list.length || !activeProject?.id) return;
    const ctx = uvmLogContextRef.current || {};
    uvmLogContextRef.current = null;
    pushItem(makeItem(THREAD_KINDS.SAID, {
      text: `Analyzing ${list.length === 1 ? list[0].name : `${list.length} simulator logs`} for UVM compile/simulation errors...`,
    }));
    try {
      const result = await uploadUvmDebugLogs(
        activeProject.id,
        list,
        {
          runId: ctx.runId,
          generatedArtifactIds: ctx.generatedArtifactIds || generatedArtifactIdsForDebug,
          simulator: "auto",
        },
        authToken,
      );
      const analysis = result?.analysis || {};
      const patches = result?.patch_proposals || [];
      const rootCauseCount = Array.isArray(analysis.root_causes) ? analysis.root_causes.length : 0;
      const patchText = patches.length
        ? ` I created ${patches.length} patch proposal${patches.length === 1 ? "" : "s"} for review.`
        : analysis.status === "passed"
          ? " No patch is needed."
          : " I did not find a safe generated-file patch yet.";
      pushItem(makeItem(THREAD_KINDS.SAID, {
        text: `${analysis.summary || "UVM log analysis finished."} Root-cause groups: ${rootCauseCount}.${patchText}`,
      }));
      patches.forEach((patch) => handleNewPatch(patch));
      await patchInbox.refresh?.();
      if (activeProject?.id) {
        await onRefreshArtifacts?.(activeProject.id, { silent: true });
      }
    } catch (err) {
      pushItem(makeItem(THREAD_KINDS.SAID, {
        text: `I couldn't analyze that simulator log: ${err?.message || "unknown error"}.`,
      }));
    }
  }, [
    activeProject?.id,
    authToken,
    generatedArtifactIdsForDebug,
    handleNewPatch,
    onRefreshArtifacts,
    patchInbox,
    pushItem,
  ]);

  const markPriorDesignPlansSuperseded = useCallback((exceptId = null) => {
    derivedItems.forEach((it) => {
      if (
        it.kind === THREAD_KINDS.PLAN
        && it.payload?._source === "rtl-design"
        && it.id !== exceptId
        && !it.payload?.superseded
      ) {
        replaceItem(it.id, (cur) => ({
          payload: { ...cur.payload, superseded: true, actions: [] },
        }));
      }
    });
  }, [derivedItems, replaceItem]);

  const handleImplementationPlanReceived = useCallback((planItemId) => {
    if (planItemId) {
      markPriorDesignPlansSuperseded(planItemId);
      planGateRef.current.requirePlanApproval = true;
      planGateRef.current.activePlanItemId = planItemId;
      saveImplementationPlanState(activeProject?.id, chatThreads.activeThreadId, {
        planItemId,
        requirePlanApproval: true,
        approved: false,
      });
      track(AnalyticsEvents.PLAN_RECEIVED, {
        project_id: activeProject?.id,
        thread_id: chatThreads.activeThreadId,
        plan_item_id: planItemId,
        source: "rtl-design",
      });
    }
  }, [
    markPriorDesignPlansSuperseded,
    activeProject?.id,
    chatThreads.activeThreadId,
  ]);

  useEffect(() => {
    onImplementationPlanRef.current = handleImplementationPlanReceived;
  }, [handleImplementationPlanReceived]);

  const handleMentalModelBuilt = useCallback((apiResult) => {
    staged.ingestMentalModelFromBuild(apiResult);
    setProjectHasMentalModel(true);
    setHasInteracted(true);
    if (!designGateRef.current.designComplete && flatArtifacts.length > 0) {
      designGateRef.current.designComplete = true;
      setDesignLifecycle("design_complete");
    }

    derivedItems.forEach((it) => {
      if (
        it.kind === THREAD_KINDS.MENTAL_MODEL
        && it.payload?._source === "overlay-build"
        && !it.payload?.superseded
      ) {
        replaceItem(it.id, (cur) => ({ payload: { ...cur.payload, superseded: true } }));
      }
    });

    track(AnalyticsEvents.MENTAL_MODEL_BUILD_COMPLETED, {
      project_id: activeProject?.id,
      source: "thread",
    });

    const model = normalizeMentalModelContent(apiResult);
    if (!model) return;

    pushItem(makeMentalModelBuiltSaid(model));
    pushItem(makeMentalModelCardItem(model, {
      source: "overlay-build",
      summaryText: apiResult?.mental_model?.summary_text,
    }));
    pushItem(makeMentalModelShowAsk());
  }, [
    staged,
    derivedItems,
    replaceItem,
    pushItem,
    flatArtifacts.length,
  ]);

  const projectHasRuns = useMemo(() => {
    if (!activeProject?.id) return false;
    return projectRuns.some((r) => String(r.project_id || "") === String(activeProject.id));
  }, [projectRuns, activeProject?.id]);

  // Mode lens must initialize before any callback that references it (TDZ crash → blank window).
  const chipixMode = useChipixMode({
    projectId: activeProject?.id,
    designLifecycle,
    hasRuns: projectHasRuns,
    hasFiles: flatArtifacts.length > 0,
  });

  useEffect(() => {
    setAnalyticsContext({
      projectId: activeProject?.id,
      threadId: chatThreads.activeThreadId,
      chipixMode: chipixMode.mode,
      organizationId: activeProject?.organization_id,
    });
  }, [
    activeProject?.id,
    activeProject?.organization_id,
    chatThreads.activeThreadId,
    chipixMode.mode,
  ]);

  const pushImportedVerificationOnboarding = useCallback(() => {
    if (designGateRef.current.mentalModelDone) return;
    if (designGateRef.current.designComplete) return;
    if (designGateRef.current.importedOnboardingShown) return;
    const rtls = artifactState?.artifacts?.rtl || [];
    if (!rtls.length) return;
    designGateRef.current.importedOnboardingShown = true;
    chipixMode.setMode(CHIPIX_MODES.VERIFY);
    setHasInteracted(true);
    pushItem(makeImportedDesignOnboardingSaid());
    pushItem(makeImportedDesignOnboardingAsk());
  }, [artifactState, pushItem, chipixMode]);

  const promptForVerificationFiles = useCallback(() => {
    const hasSpec = (artifactState?.artifacts?.spec || []).length > 0;
    const hasRtl = (artifactState?.artifacts?.rtl || []).length > 0;
    if (hasSpec && hasRtl) return false;
    if (designGateRef.current.verifyUploadAskShown) return true;
    designGateRef.current.verifyUploadAskShown = true;
    setHasInteracted(true);
    chipixMode.setMode(CHIPIX_MODES.VERIFY);
    pushItem(makeVerificationUploadAsk({ hasSpec, hasRtl }));
    return true;
  }, [artifactState, chipixMode, pushItem]);

  const beginStagedVerification = useCallback(() => {
    if (promptForVerificationFiles()) {
      setComposerAttachHint("Upload spec and RTL before verification.");
      return;
    }
    setHasInteracted(true);
    chipixMode.setMode(CHIPIX_MODES.VERIFY);
    if (!designGateRef.current.mentalModelDone) {
      void staged.prepareThenReviewMentalModel(
        designGateRef.current.designComplete ? "design-gate" : "imported",
      );
      return;
    }
    void staged.recommend();
  }, [chipixMode, promptForVerificationFiles, staged]);

  const composerPlaceholder = useMemo(() => {
    if (chipixMode.mode === CHIPIX_MODES.VERIFY) {
      if (chipixMode.derivedPhase === "empty") {
        return "Verify what? Attach RTL, or ask me to bring a design first…";
      }
      if (chipixMode.derivedPhase === "ready-to-verify") {
        return "Say the word — \"plan\", \"run formal only\", \"why did that fail?\"…";
      }
      return "Ask anything verification — strategy, plan, why a check failed…";
    }
    if (chipixMode.derivedPhase === "empty") {
      return "Describe a chip in plain English — \"Build a UART with APB.\"";
    }
    if (chipixMode.derivedPhase === "mental-model") {
      return "Refine the design, or say \"looks right\" to confirm the mental model…";
    }
    return "Describe what to design, refine, or change…";
  }, [chipixMode.mode, chipixMode.derivedPhase]);

  // RTL language + design-time composer chrome — only while the thread is still
  // in the design step (before post-design / mental-model / verification gates).
  const isInDesigningPhase = useMemo(() => {
    if (chipixMode.mode !== CHIPIX_MODES.DESIGN) return false;
    if (designLifecycle !== "idle") return false;
    if (clarifier.isOpen) return false;
    if (rtlStreaming) return false;
    if (!runBelongsToThread) return true;
    const runStat = String(activeRunStatus?.status || "").toLowerCase();
    if (runStat === "running" || runStat === "queued") return false;
    if (runStat === "completed" || runStat === "passed") return false;
    return true;
  }, [
    chipixMode.mode,
    designLifecycle,
    clarifier.isOpen,
    rtlStreaming,
    runBelongsToThread,
    activeRunStatus?.status,
  ]);

  const showHdlLanguagePicker = isInDesigningPhase;

  // Health
  const health = backendConnectivity?.status === "ok"
    ? (rtlStreaming || String(activeRunStatus?.status || "").toLowerCase() === "running" ? "busy" : "ok")
    : "bad";
  const healthLabel = backendConnectivity?.status === "ok"
    ? (rtlStreaming ? "Designing…" : String(activeRunStatus?.status || "").toLowerCase() === "running" ? "Verifying…" : "Everything healthy")
    : backendConnectivity?.message || "Backend issue";

  // Workspace context — structured metadata attached to every agent message
  // so the agent can resolve "fix this", "explain that file" without us
  // shipping raw file content in the prompt every turn (cache-busting).
  const workspaceAttachExistingKeys = useMemo(
    () => composerAttachments.map((r) => attachmentRowKey(r)),
    [composerAttachments],
  );

  const composerAttachmentChips = useMemo(() => {
    const chips = [];
    uploadJobs.forEach((j) => {
      chips.push({
        key: j.id,
        label: j.label,
        kind: j.kind === ARTIFACT_KIND.RTL_FOLDER ? ARTIFACT_KIND.RTL : j.kind,
        kindLabel: kindLabel(j.kind),
        status: j.status,
        error: j.error,
        removable: j.status === "error",
      });
    });

    if (chipixMode.mode === CHIPIX_MODES.VERIFY) {
      const specs = artifactState?.artifacts?.spec || [];
      const rtls = artifactState?.artifacts?.rtl || [];
      const activeSpecId = artifactState?.active?.active_spec_artifact_id || null;
      const activeRtlId = artifactState?.active?.active_rtl_artifact_id || null;
      const activeSpec = activeSpecId ? specs.find((a) => a.id === activeSpecId) : null;
      const activeRtl = activeRtlId ? rtls.find((a) => a.id === activeRtlId) : null;
      const uploadingSpec = uploadJobs.some(
        (j) => j.kind === ARTIFACT_KIND.SPEC && j.status === "uploading",
      );
      const uploadingRtl = uploadJobs.some(
        (j) => (j.kind === ARTIFACT_KIND.RTL || j.kind === ARTIFACT_KIND.RTL_FOLDER) && j.status === "uploading",
      );

      if (!activeSpec && !uploadingSpec) {
        chips.push({
          key: "project-spec-missing",
          label: "Not attached",
          kind: ARTIFACT_KIND.SPEC,
          kindLabel: "Spec",
          status: "missing",
          removable: false,
        });
      } else if (activeSpec) {
        chips.push({
          key: `project-spec-${activeSpec.id}`,
          artifactId: activeSpec.id,
          label: artifactDisplayName(activeSpec),
          kind: ARTIFACT_KIND.SPEC,
          kindLabel: "Spec",
          status: "ready",
          removable: true,
        });
      }

      if (!activeRtl && !uploadingRtl) {
        chips.push({
          key: "project-rtl-missing",
          label: "Not attached",
          kind: ARTIFACT_KIND.RTL,
          kindLabel: "RTL",
          status: "missing",
          removable: false,
        });
      } else if (activeRtl) {
        chips.push({
          key: `project-rtl-${activeRtl.id}`,
          artifactId: activeRtl.id,
          label: artifactDisplayName(activeRtl),
          kind: ARTIFACT_KIND.RTL,
          kindLabel: "RTL",
          status: "ready",
          removable: true,
        });
      }
    }

    composerAttachments.forEach((r) => {
      chips.push({
        key: attachmentRowKey(r),
        label: r.label || r.artifactId || "file",
        kind: ARTIFACT_KIND.CONTEXT,
        kindLabel: "Context",
        status: "ready",
        removable: true,
      });
    });

    return chips;
  }, [uploadJobs, artifactState, composerAttachments, chipixMode.mode]);

  const mainComposerTokenContext = useMemo(() => buildComposerTokenContext({
    value: composer,
    attachmentChips: composerAttachmentChips,
    messages: chatThreads.chatMessages,
    projectTokenUsage,
  }), [composer, composerAttachmentChips, chatThreads.chatMessages, projectTokenUsage]);

  const composerAttachStatusLine = useMemo(() => {
    if (composerAttachHint) return composerAttachHint;
    if (uploadJobs.some((j) => j.status === "uploading")) return "Adding files to the project…";
    const failedUpload = uploadJobs.find((j) => j.status === "error");
    if (failedUpload) {
      return `Upload failed: ${failedUpload.error || "remove the failed chip and try again."}`;
    }
    if (chipixMode.mode !== CHIPIX_MODES.VERIFY) return "";
    const hasSpec = Boolean(artifactState?.active?.active_spec_artifact_id);
    const hasRtl = Boolean(artifactState?.active?.active_rtl_artifact_id);
    if (hasSpec && hasRtl) return "Specification and RTL are attached — ready when you are.";
    if (!hasSpec && !hasRtl) return "Attach a specification and RTL to run verification.";
    if (!hasRtl) return "RTL is required before verification can run.";
    if (!hasSpec) return "Specification recommended — attach when ready.";
    if (chipixMode.mode === CHIPIX_MODES.VERIFY && designLifecycle !== "mm_done") {
      return "Next: build a mental model from your files, then verification can start.";
    }
    const hasWorkspace = hasSpec || hasRtl;
    if (hasWorkspace && !chatThreads.chatMessages?.length) {
      return "This project already has files — say “new design” or “modify existing RTL” so the agent does not overwrite.";
    }
    return "";
  }, [composerAttachHint, uploadJobs, chipixMode.mode, artifactState, designLifecycle, chatThreads.chatMessages]);

  const handleWorkspaceAttachConfirm = useCallback((rows) => {
    const artById = new Map();
    (flatArtifacts || []).forEach((a) => {
      artById.set(a.id, a);
      if (a.artifactId) artById.set(a.artifactId, a);
    });
    const enriched = rows.map((r) => {
      const art = artById.get(r.artifactId);
      return {
        ...r,
        artifactKind: r.artifactKind || (art ? artifactKindFromRecord(art) : ARTIFACT_KIND.CONTEXT),
      };
    });
    setComposerAttachments((prev) => {
      const map = new Map();
      prev.forEach((r) => map.set(attachmentRowKey(r), r));
      enriched.forEach((r) => map.set(attachmentRowKey(r), r));
      return Array.from(map.values()).slice(0, MAX_ATTACH);
    });
    setWorkspaceAttachOpen(false);

    const specId = enriched.find((r) => r.artifactKind === ARTIFACT_KIND.SPEC)?.artifactId;
    const rtlId = enriched.find((r) => (
      r.artifactKind === ARTIFACT_KIND.RTL || r.artifactKind === ARTIFACT_KIND.RTL_FOLDER
    ))?.artifactId;
    if (activeProject?.id && authToken && (specId || rtlId)) {
      void setActiveProjectArtifacts(
        activeProject.id,
        { specArtifactId: specId, rtlArtifactId: rtlId },
        authToken,
      )
        .then(() => onRefreshArtifacts?.(activeProject.id, { silent: true }))
        .catch(() => {
          // Backend prepare/build also falls back to latest spec/RTL if pointers are unset.
        });
    }
  }, [flatArtifacts, activeProject?.id, authToken, onRefreshArtifacts]);

  const removeComposerAttachment = useCallback((key) => {
    setComposerAttachments((prev) => prev.filter((r) => attachmentRowKey(r) !== key));
  }, []);

  const handleRemoveComposerAttachment = useCallback((key) => {
    const specPrefix = "project-spec-";
    const rtlPrefix = "project-rtl-";
    const activeKind = key?.startsWith(specPrefix)
      ? ARTIFACT_KIND.SPEC
      : key?.startsWith(rtlPrefix)
        ? ARTIFACT_KIND.RTL
        : null;

    if (activeKind && activeProject?.id && authToken) {
      const artifactId = key.slice(activeKind === ARTIFACT_KIND.SPEC ? specPrefix.length : rtlPrefix.length);
      setComposerAttachHint(
        activeKind === ARTIFACT_KIND.SPEC
          ? "Removing specification file..."
          : "Removing RTL file...",
      );
      void deleteProjectArtifact(activeProject.id, artifactId, authToken)
        .then(() => setActiveProjectArtifacts(
          activeProject.id,
          activeKind === ARTIFACT_KIND.SPEC
            ? { specArtifactId: null }
            : { rtlArtifactId: null },
          authToken,
        ))
        .then(() => {
          if (editorTarget?.artifactId === artifactId) closeFile();
          setComposerAttachHint(
            activeKind === ARTIFACT_KIND.SPEC
              ? "Specification file removed from this project."
              : "RTL file removed from this project.",
          );
          return Promise.resolve(onRefreshArtifacts?.(activeProject.id, { silent: true }));
        })
        .catch((err) => {
          setComposerAttachHint(`Remove failed: ${err?.message || "try again."}`);
        });
      return;
    }

    setUploadJobs((prev) => prev.filter((j) => j.id !== key));
    removeComposerAttachment(key);
  }, [
    activeProject?.id,
    authToken,
    closeFile,
    editorTarget?.artifactId,
    onRefreshArtifacts,
    removeComposerAttachment,
  ]);

  const openWorkspaceAttach = useCallback(() => {
    if (!activeProject?.id) {
      setComposerAttachHint("Pick a project from the top-left, then attach files.");
      return;
    }
    setWorkspaceAttachOpen(true);
  }, [activeProject?.id]);

  const workspaceContext = useMemo(() => {
    const ctx = {};
    if (editorTarget) {
      ctx.active_file_path = editorTarget.name || editorTarget.memberPath || null;
      ctx.active_file_id = editorTarget.artifactId || null;
      if (editorTarget.focusLine) ctx.active_file_line = editorTarget.focusLine;
    }
    if (activeRunId) {
      ctx.last_run_id = activeRunId;
      const status = String(activeRunStatus?.status || "").toLowerCase();
      if (status) ctx.last_run_status = status;
    }
    if (designHdlLanguage) {
      ctx.hdl_language = designHdlLanguage;
      ctx.code_style = designHdlLanguage;
    }
    return ctx;
  }, [editorTarget, activeRunId, activeRunStatus?.status, designHdlLanguage]);

  const sendIdeWithContext = useCallback(async (text, options = {}) => {
    const slice = (options.attachments ?? ideAttachments).slice(0, MAX_ATTACH);
    const attached_file_contents = [];
    const workspace_file_labels = [];

    if (slice.length && onFetchArtifactContent) {
      for (const row of slice) {
        try {
          const content = await onFetchArtifactContent(row.artifactId, {
            memberPath: row.memberPath || undefined,
          });
          const filename = row.label || row.artifactId || "file";
          workspace_file_labels.push(filename);
          const capped = (content || "").slice(0, 32000);
          if (capped.trim()) {
            attached_file_contents.push({ filename, content: capped });
          }
        } catch {
          workspace_file_labels.push(row.label || row.artifactId || "file");
        }
      }
    }

    const mainThread = (chatThreads.threads || []).find(
      (t) => t.id === chatThreads.activeThreadId,
    );
    const recap = buildThreadRecap(chatThreads.chatMessages, 12);
    const ctx = {
      ...workspaceContext,
      ...(options.context || {}),
      conversation_surface: "ide",
      ide_assistant: true,
      source_thread_id: chatThreads.activeThreadId,
      source_thread_title: mainThread?.title || "Project Chat",
    };
    if (recap) ctx.thread_recap_json = recap;
    if (attached_file_contents.length) ctx.attached_file_contents = attached_file_contents;
    if (workspace_file_labels.length) ctx.workspace_files = workspace_file_labels;

    await sendIdeMessage(text, {
      ...options,
      context: ctx,
      language: options.language || designHdlLanguage,
    });
    setIdeAttachments([]);
  }, [
    sendIdeMessage,
    workspaceContext,
    ideAttachments,
    onFetchArtifactContent,
    chatThreads.activeThreadId,
    chatThreads.threads,
    chatThreads.chatMessages,
    designHdlLanguage,
  ]);

  const sendRtlWithContext = useCallback(async (text, options = {}) => {
    const slice = (options.attachments ?? composerAttachments).slice(0, MAX_ATTACH);
    const attached_file_contents = [];
    const workspace_file_labels = [];
    const manifest = buildProjectArtifactManifest(artifactState);
    const projectWorkspaceFiles = workspaceFilesFromManifest(manifest);

    if (slice.length && onFetchArtifactContent) {
      for (const row of slice) {
        try {
          const content = await onFetchArtifactContent(row.artifactId, {
            memberPath: row.memberPath || undefined,
          });
          const filename = row.label || row.artifactId || "file";
          workspace_file_labels.push(filename);
          const capped = (content || "").slice(0, 32000);
          if (capped.trim()) {
            attached_file_contents.push({ filename, content: capped });
          }
        } catch {
          workspace_file_labels.push(row.label || row.artifactId || "file");
        }
      }
    }

    const agentMode = options.mode
      || options.context?.mode
      || (chipixMode.mode === CHIPIX_MODES.VERIFY ? "verification" : "rtl_designer");
    const ctx = {
      ...workspaceContext,
      mode: agentMode,
      chipix_mode: chipixMode.mode,
      ...(options.context || {}),
    };
    if (manifest.artifact_count > 0) {
      ctx.project_artifact_manifest = manifest;
      if (!ctx.workspace_files) {
        ctx.workspace_files = projectWorkspaceFiles;
      }
      if (!ctx.workspace_mode) {
        ctx.workspace_mode = options.workspaceMode || "extend_existing";
      }
    } else if (!ctx.workspace_mode) {
      ctx.workspace_mode = options.workspaceMode || "greenfield";
    }
    if (chatThreads.activeThreadId) {
      ctx.thread_id = chatThreads.activeThreadId;
    }
    const activeThread = (chatThreads.threads || []).find(
      (t) => t.id === chatThreads.activeThreadId,
    );
    if (activeThread?.title) {
      ctx.thread_title = activeThread.title;
    }
    if (!ctx.thread_recap_json) {
      const recap = buildThreadRecap(chatThreads.chatMessages, 12);
      if (recap) ctx.thread_recap_json = recap;
    }
    if (attached_file_contents.length) {
      ctx.attached_file_contents = attached_file_contents;
    }
    if (workspace_file_labels.length) {
      ctx.workspace_files = workspace_file_labels;
    }
    if (planGateRef.current.requirePlanApproval) {
      ctx.require_plan_approval = true;
    }

    await sendRtlMessage(text, {
      ...options,
      mode: agentMode,
      context: ctx,
      language: options.language || designHdlLanguage,
    });
    setComposerAttachments([]);
  }, [
    sendRtlMessage,
    workspaceContext,
    composerAttachments,
    artifactState,
    onFetchArtifactContent,
    chatThreads.activeThreadId,
    chatThreads.threads,
    chatThreads.chatMessages,
    designHdlLanguage,
    chipixMode.mode,
  ]);

  const handleClarifierComplete = useCallback(() => {
    const { message, recap, turnId } = clarifier.buildCompletion();
    clarifier.close();
    track(AnalyticsEvents.CLARIFIER_COMPLETED, {
      project_id: activeProject?.id,
      thread_id: chatThreads.activeThreadId,
    });
    if (!message?.trim()) return;
    pushItem(makeItem(THREAD_KINDS.USER, { text: message }, { turnId: turnId || undefined }));
    if (recap) {
      pushItem(makeItem(THREAD_KINDS.SAID, { text: recap }));
    }
    setHasInteracted(true);
    planGateRef.current.requirePlanApproval = true;
    const agentMessage = [
      message,
      "",
      "[System note for the agent only: All clarifications are complete. Emit exactly one `chipix:plan` JSON fence consolidating these answers. Do not ask more questions or call createFile until the user clicks Implement on the plan.]",
    ].join("\n");
    void sendRtlWithContext(agentMessage, {
      mode: "rtl_designer",
      context: {
        require_plan_approval: true,
        clarifier_complete: true,
        mode: "rtl_designer",
      },
    });
  }, [clarifier, pushItem, sendRtlWithContext]);

  const handleClarifierDismiss = useCallback(() => {
    clarifier.close();
    track(AnalyticsEvents.CLARIFIER_DISMISSED, {
      project_id: activeProject?.id,
      thread_id: chatThreads.activeThreadId,
    });
    pushItem(makeItem(THREAD_KINDS.SAID, {
      text: "No problem — type your answers in the chat when you're ready.",
    }));
  }, [activeProject?.id, chatThreads.activeThreadId, clarifier, pushItem]);

  const { prepareRewindFromUserItem } = useThreadRewind({
    items: derivedItems,
    extras,
    setExtras,
    truncateChatMessages: chatThreads.truncateMessagesAfter,
    authToken,
    threadId: chatThreads.activeThreadId,
    onRefreshArtifacts,
  });

  const handleEditUserMessage = useCallback(async (item) => {
    if (rtlStreaming) cancelRtlStream();
    try {
      const result = await prepareRewindFromUserItem(item);
      if (!result?.text) return;
      setComposer(result.text);
      setHasInteracted(true);
      setForceGreenfieldStarters(false);
    } catch (err) {
      pushItem(makeItem(THREAD_KINDS.SAID, {
        text: `Could not rewind the conversation: ${err.message || "unknown error"}`,
      }));
    }
  }, [
    rtlStreaming,
    cancelRtlStream,
    prepareRewindFromUserItem,
    pushItem,
  ]);

  // Backfill association: if a run becomes active (e.g. via a card or
  // overlay path) while a thread is focused and we have prior conversation
  // activity, stamp the association so the run shows up here rather than
  // disappearing into limbo.
  useEffect(() => {
    if (!activeRunId || !chatThreads.activeThreadId) return;
    if (!hasThreadActivity) return;
    setRunForThread((prev) => {
      if (prev[chatThreads.activeThreadId] === activeRunId) return prev;
      // Don't steal a run already claimed by another thread.
      const claimedBy = Object.entries(prev).find(([, rid]) => rid === activeRunId);
      if (claimedBy && claimedBy[0] !== chatThreads.activeThreadId) return prev;
      const next = { ...prev, [chatThreads.activeThreadId]: activeRunId };
      if (runForThreadStorageKey) {
        try { window.localStorage.setItem(runForThreadStorageKey, JSON.stringify(next)); } catch { /* quota / private mode */ }
      }
      return next;
    });
  }, [activeRunId, chatThreads.activeThreadId, hasThreadActivity, runForThreadStorageKey]);

  // Project subtitle — state-aware. Reads the same signals as health but
  // surfaces the current narrative step to the user in the project chip.
  const projectSubtitle = useMemo(() => {
    if (!activeProject) return undefined;
    const runStatus = String(activeRunStatus?.status || "").toLowerCase();
    if (rtlStreaming) return "designing now";
    if (runStatus === "running" || runStatus === "queued") return "verifying now";
    if (runStatus === "failed" || runStatus === "interrupted" || runStatus === "cancelled") return "last run failed";
    if (runStatus === "completed" || runStatus === "passed") return "last run passed";
    if (designLifecycle === "design_complete") return "design done · mental model next";
    if (designLifecycle === "mm_done") return "ready to verify";
    return "new conversation";
  }, [activeProject, rtlStreaming, activeRunStatus?.status, designLifecycle]);

  // Mark interaction whenever a turn is added to hide welcome.
  useEffect(() => {
    if (derivedItems.length > 0) setHasInteracted(true);
  }, [derivedItems.length]);

  // Auto-scroll on new items.
  useEffect(() => {
    const el = threadScrollRef.current;
    if (!el) return;
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
  }, [derivedItems.length]);

  // Global shortcuts.
  useEffect(() => {
    const handler = (e) => {
      const isMeta = e.metaKey || e.ctrlKey;
      if (isMeta && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (clarifier.isOpen) {
          clarifier.close();
          return;
        }
        if (dashOpen || tasksOpen || mmOpen || ideOpen || docsOpen || foundersOpen || editorTarget) return;
        setPaletteOpen(true);
      } else if (isMeta && e.shiftKey && e.key.toLowerCase() === "e") {
        e.preventDefault();
        if (paletteOpen || drawerOpen || dashOpen || mmOpen || ideOpen || docsOpen || foundersOpen || editorTarget) return;
        openPrimary("ide");
      } else if (e.key === "Escape") {
        if (clarifier.isOpen) handleClarifierDismiss();
        else if (paletteOpen) setPaletteOpen(false);
        else if (drawerOpen) setDrawerOpen(false);
        else if (ideAttachModalOpen) setIdeAttachModalOpen(false);
        else if (workspaceAttachOpen) setWorkspaceAttachOpen(false);
        else if (goBack()) { /* surface or file peek */ }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [
    paletteOpen,
    drawerOpen,
    dashOpen,
    mmOpen,
    ideOpen,
    docsOpen,
    foundersOpen,
    editorTarget,
    clarifier.isOpen,
    handleClarifierDismiss,
    workspaceAttachOpen,
    ideAttachModalOpen,
    goBack,
    openPrimary,
  ]);

  // ─────────────────────────────────────────────────────────────────────
  // Send + starter handling
  // ─────────────────────────────────────────────────────────────────────

  // True if the last thing in the thread reads as the close of a prior chapter
  // (a verdict, a completed run summary, a final agent message). Used to
  // decide whether the next user message should be preceded by a section break.
  const lastItemClosesChapter = useCallback(() => {
    const list = derivedItems;
    if (!list.length) return false;
    for (let i = list.length - 1; i >= 0; i -= 1) {
      const it = list[i];
      if (it?.kind === THREAD_KINDS.THINKING) continue;
      if (it?.kind === THREAD_KINDS.VERDICT) return true;
      if (it?.kind === THREAD_KINDS.RUN) {
        const status = String(it.payload?.status || "").toLowerCase();
        if (["completed", "passed", "failed", "cancelled", "interrupted"].includes(status)) return true;
      }
      // First non-thinking item that isn't terminal → not a chapter close.
      return false;
    }
    return false;
  }, [derivedItems]);

  const formatBreakLabel = useCallback(() => {
    const d = new Date();
    let h = d.getHours();
    const m = String(d.getMinutes()).padStart(2, "0");
    const ap = h >= 12 ? "PM" : "AM";
    if (h > 12) h -= 12;
    if (h === 0) h = 12;
    return `Iteration · ${h}:${m} ${ap}`;
  }, []);

  const hasPendingMentalModelApproval = useMemo(
    () => derivedItems.some((it) => (
      it?.kind === THREAD_KINDS.MENTAL_MODEL
      && !it.payload?.approved
      && !it.payload?.superseded
    )),
    [derivedItems],
  );

  const stagedVerdictPayload = useMemo(() => {
    for (let i = derivedItems.length - 1; i >= 0; i -= 1) {
      const it = derivedItems[i];
      if (it?.kind === THREAD_KINDS.VERDICT && it.payload?.verificationSource === "staged_verification") {
        return it.payload;
      }
    }
    return null;
  }, [derivedItems]);

  const triggerCadenceVerification = useCallback((message, extraContext = {}, { useAgent = false } = {}) => {
    const ctx = buildCadenceVerificationContext({
      projectId: activeProject?.id,
      executionResult: staged.snapshot?.executionResult,
      verdictPayload: stagedVerdictPayload,
      selectedModule: extraContext.selectedModule,
      mentalModelRevisionId: extraContext.mentalModelRevisionId,
    });
    const mergedContext = {
      ...ctx,
      ...extraContext,
      mode: extraContext.mode || "verification",
    };
    const artifactIds = mergedContext.generated_artifact_ids
      || stagedVerdictPayload?.generatedArtifactIds
      || [];
    const topModule = mergedContext.uvm_top_module || "top_tb";
    const uvmTestname = mergedContext.uvm_testname || "";

    if (!useAgent && !message) {
      void runCadenceDirect({
        generatedArtifactIds: artifactIds,
        topModule,
        uvmTestname,
      });
      return;
    }

    void sendRtlWithContext(
      message || "Run the full Cadence Xcelium simulation on the generated UVM testbench.",
      {
        context: mergedContext,
        allowAgentVerificationStart: true,
      },
    );
  }, [
    activeProject?.id,
    runCadenceDirect,
    sendRtlWithContext,
    staged.snapshot?.executionResult,
    stagedVerdictPayload,
  ]);

  const hasUvmArtifacts = useMemo(
    () => Boolean(
      generatedArtifactIdsForDebug.length
      || stagedVerdictPayload?.generatedArtifactIds?.length,
    ),
    [generatedArtifactIdsForDebug.length, stagedVerdictPayload],
  );

  const handleMentalModelGraphChat = useCallback((text, { selectedNode, revisionId } = {}) => {
    setGraphChatMessages((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, role: "user", text },
    ]);
    const cadenceCtx = isCadenceRunPrompt(text)
      ? buildCadenceVerificationContext({
          projectId: activeProject?.id,
          verdictPayload: stagedVerdictPayload,
        })
      : {};
    void sendRtlWithContext(text, {
      allowAgentVerificationStart: true,
      context: {
        mode: "mental_model",
        mental_model_chat: true,
        conversation_surface: "mental_model_graph",
        selected_module: selectedNode || undefined,
        mental_model_revision_id: revisionId || undefined,
        ...cadenceCtx,
        ...(isCadenceRunPrompt(text) ? { cadence_verification_mode: true } : {}),
      },
    });
  }, [sendRtlWithContext, stagedVerdictPayload]);

  const handleMentalModelCadenceAction = useCallback(({ action, selectedNode }) => {
    if (action === "run") {
      triggerCadenceVerification(
        selectedNode
          ? `Run Cadence Xcelium simulation for module ${selectedNode} using the generated UVM testbench.`
          : undefined,
        { selectedModule: selectedNode, mode: "mental_model", mental_model_chat: true },
      );
      return;
    }
    if (action === "explain") {
      void sendRtlWithContext("Explain the last Cadence simulation run and what still needs attention.", {
        allowAgentVerificationStart: true,
        context: {
          mode: "mental_model",
          mental_model_chat: true,
          selected_module: selectedNode || undefined,
          ...buildCadenceVerificationContext({
            projectId: activeProject?.id,
            verdictPayload: stagedVerdictPayload,
          }),
        },
      });
    }
  }, [triggerCadenceVerification, sendRtlWithContext, stagedVerdictPayload]);

  const handleSend = useCallback(async (overrideText, sendOptions = {}) => {
    const raw = (overrideText ?? composer).trim();
    const fallback = "I've attached workspace files for context — use them for this turn.";
    const text = raw || (composerAttachments.length ? fallback : "");
    if (!text.trim()) return;
    if (!activeProject?.id) {
      pushItem(makeItem(THREAD_KINDS.SAID, {
        text: "Pick a project from the top-left to get started.",
      }));
      return;
    }
    setComposer("");
    setHasInteracted(true);
    setForceGreenfieldStarters(false);
    playSwoosh();
    if (lastItemClosesChapter()) {
      pushItem(makeItem(THREAD_KINDS.BREAK, { label: formatBreakLabel() }));
    }
    if (!sendOptions?.allowAgentVerificationStart && isVerificationStartPrompt(text)) {
      pushItem(makeItem(THREAD_KINDS.USER, { text }));
      if (!designGateRef.current.mentalModelDone && hasPendingMentalModelApproval) {
        pushItem(makeItem(THREAD_KINDS.SAID, {
          text: "I have the mental model checkpoint ready above. Click **Looks right — continue** first, then I'll plan verification from that exact model.",
        }));
        return;
      }
      beginStagedVerification();
      return;
    }
    const mergedOptions = { ...sendOptions };
    if (
      isCadenceRunPrompt(text)
      && !mergedOptions?.context?.cadence_verification_mode
    ) {
      mergedOptions.context = {
        ...(mergedOptions.context || {}),
        ...buildCadenceVerificationContext({
          projectId: activeProject?.id,
          executionResult: staged.snapshot?.executionResult,
          verdictPayload: stagedVerdictPayload,
        }),
      };
    }
    await sendRtlWithContext(text, mergedOptions);
  }, [
    composer,
    composerAttachments,
    activeProject?.id,
    sendRtlWithContext,
    pushItem,
    lastItemClosesChapter,
    formatBreakLabel,
    hasPendingMentalModelApproval,
    beginStagedVerification,
    staged.snapshot?.executionResult,
    stagedVerdictPayload,
  ]);

  const handleStarter = useCallback((starter) => {
    if (starter.id === "import") {
      pushItem(makeItem(THREAD_KINDS.USER, { text: starter.seed }));
      pushItem(makeItem(THREAD_KINDS.ASK, {
        question: "How would you like to bring in your design?",
        sub: "Pick the option that matches what you have today.",
        choices: [
          { id: "upload-rtl", label: "Upload RTL files" },
          { id: "upload-rtl-folder", label: "Upload an RTL folder (preserves hierarchy)" },
          { id: "upload-spec", label: "Upload a spec document" },
          { id: "build-mental-model", label: "Build mental model, then verify" },
        ],
      }));
      setHasInteracted(true);
      return;
    }
    setHasInteracted(true);
    // Adaptive starters can dispatch to first-class actions directly
    // (no chat round-trip needed) when their `action` is set.
    if (starter.action === "verify" || starter.action === "import-verify") {
      beginStagedVerification();
      return;
    }
    if (starter.action === "mental-model") {
      void staged.prepareThenReviewMentalModel("imported");
      setHasInteracted(true);
      return;
    }
    if (starter.action === "dashboard") {
      openProjectDashboard();
      return;
    }
    if (starter.action === "switch-to-design") {
      chipixMode.setMode(CHIPIX_MODES.DESIGN);
      return;
    }
    if (starter.action === "reset") {
      // "Start a new design" — open a fresh chat thread, request greenfield
      // starters (UART/FIFO/ALU/Existing) so the user can pick one in a
      // single follow-up click rather than navigate the iterating set again.
      clearItems();
      setHasInteracted(false);
      setForceGreenfieldStarters(true);
      void chatThreads.createThread({ title: "New design", switchTo: true });
      return;
    }
    if (starter.seed) {
      handleSend(starter.seed);
    }
  }, [handleSend, pushItem, beginStagedVerification, staged, clearItems, chatThreads, openProjectDashboard, chipixMode]);

  // ─────────────────────────────────────────────────────────────────────
  // Artifact upload (composer attach + ask flow)
  // ─────────────────────────────────────────────────────────────────────
  const triggerAttach = useCallback((kind = ARTIFACT_KIND.RTL) => {
    if (!activeProject?.id) {
      setComposerAttachHint("Pick a project from the top-left, then attach files.");
      return;
    }
    setComposerAttachHint("");
    pendingAttachKindRef.current = kind;
    setPendingAttachKind(kind);
    if (kind === ARTIFACT_KIND.SPEC) specInputRef.current?.click();
    else if (kind === ARTIFACT_KIND.RTL_FOLDER) folderInputRef.current?.click();
    else fileInputRef.current?.click();
  }, [activeProject?.id]);

  const uploadArtifactFiles = useCallback(async (list, kind = ARTIFACT_KIND.RTL, source = "thread_first_ui") => {
    if (!list.length || !activeProject?.id || !onUploadArtifact) return;
    const jobId = makeUploadJobId();
    const label = uploadLabelForFiles(list, kind);
    setUploadJobs((prev) => [...prev, { id: jobId, label, kind, status: "uploading" }]);
    try {
      if (kind === ARTIFACT_KIND.SPEC && list.length > 1) {
        for (const file of list) {
          // eslint-disable-next-line no-await-in-loop
          await onUploadArtifact({
            artifactType: "spec",
            file,
            source,
          });
        }
      } else if (kind === ARTIFACT_KIND.RTL_FOLDER || (kind === ARTIFACT_KIND.RTL && list.length > 1)) {
        await onUploadArtifact({
          artifactType: "rtl",
          files: list.map((file) => ({ file, relativePath: file.webkitRelativePath || file.name })),
          archiveName: `rtl_${Date.now()}.zip`,
          source,
          preserveHierarchy: kind === ARTIFACT_KIND.RTL_FOLDER,
        });
      } else {
        await onUploadArtifact({
          artifactType: kind === ARTIFACT_KIND.SPEC ? "spec" : "rtl",
          file: list[0],
          source,
        });
      }
      setUploadJobs((prev) => prev.map((j) => (
        j.id === jobId ? { ...j, status: "ready", label } : j
      )));
      designGateRef.current.verifyUploadAskShown = false;
      window.setTimeout(() => {
        setUploadJobs((prev) => prev.filter((j) => j.id !== jobId));
      }, 3200);
    } catch (err) {
      const message = uploadErrorMessage(err);
      setUploadJobs((prev) => prev.map((j) => (
        j.id === jobId
          ? { ...j, status: "error", error: message }
          : j
      )));
      throw err;
    }
  }, [activeProject?.id, onUploadArtifact]);

  const inferUploadKind = useCallback((fileList) => {
    const list = Array.from(fileList || []);
    if (!list.length) return ARTIFACT_KIND.RTL;
    const specPattern = /\.(md|markdown|txt|text|pdf|docx?|json|ya?ml|rst|csv|log|html?|xml)$/i;
    if (list.every((f) => specPattern.test(f.name || ""))) return ARTIFACT_KIND.SPEC;
    if (list.length > 1) return ARTIFACT_KIND.RTL;
    return specPattern.test(list[0].name || "") ? ARTIFACT_KIND.SPEC : ARTIFACT_KIND.RTL;
  }, []);

  const handleFilesPicked = useCallback(async (event) => {
    const list = Array.from(event.target.files || []);
    event.target.value = "";
    const kind = pendingAttachKindRef.current || pendingAttachKind || ARTIFACT_KIND.RTL;
    pendingAttachKindRef.current = null;
    setPendingAttachKind(null);
    if (!list.length || !activeProject?.id || !onUploadArtifact) return;

    try {
      await uploadArtifactFiles(list, kind);
      const uploadedRtl = kind === ARTIFACT_KIND.RTL || kind === ARTIFACT_KIND.RTL_FOLDER;
      if (uploadedRtl) {
        window.setTimeout(() => pushImportedVerificationOnboarding(), 500);
      }
    } catch {
      /* uploadArtifactFiles updates upload job status */
    }
  }, [pendingAttachKind, activeProject?.id, onUploadArtifact, uploadArtifactFiles, pushImportedVerificationOnboarding]);

  // ─────────────────────────────────────────────────────────────────────
  // Card handlers
  // ─────────────────────────────────────────────────────────────────────
  const handleAskChoose = useCallback((itemId, choiceId) => {
    const item = derivedItems.find((it) => it.id === itemId);
    const choice = item?.payload?.choices?.find((c) => c.id === choiceId);
    const label = choice?.label || choiceId;
    const gate = item?.payload?._gate;
    const isVerificationUploadAction = gate === "verification-upload"
      && ["upload-rtl", "upload-rtl-folder", "upload-spec", "gate-open-ide"].includes(choiceId);

    if (!isVerificationUploadAction) {
      replaceItem(itemId, (cur) => ({
        payload: { ...cur.payload, chosenId: choiceId, locked: true },
      }));
    }

    if (choiceId === "start-verification" || choiceId === "build-mental-model") {
      beginStagedVerification();
      return;
    }
    if (choiceId === "upload-rtl") {
      triggerAttach(ARTIFACT_KIND.RTL);
      return;
    }
    if (choiceId === "upload-rtl-folder") {
      triggerAttach(ARTIFACT_KIND.RTL_FOLDER);
      return;
    }
    if (choiceId === "upload-spec") {
      triggerAttach(ARTIFACT_KIND.SPEC);
      return;
    }
    if (choiceId === "show-mental-model") {
      openPrimary("mental");
      return;
    }

    if (gate === "mental-model-ready") {
      if (choiceId === "gate-mm-confirm") {
        designGateRef.current.mentalModelDone = true;
        setDesignLifecycle("mm_done");
        pushVerificationGate();
        return;
      }
    }
    if (gate === GATE.POST_DESIGN || gate === GATE.IMPORTED) {
      if (choiceId === "gate-open-ide") {
        openIdeFromArtifacts();
        return;
      }
      if (choiceId === "gate-build-mental-model") {
        if (promptForVerificationFiles()) return;
        void staged.prepareThenReviewMentalModel(
          gate === GATE.IMPORTED ? "imported" : "design-gate",
        );
        return;
      }
    }
    if (gate === GATE.VERIFY || gate === "verification-upload") {
      if (choiceId === "gate-start-verification") {
        beginStagedVerification();
        return;
      }
      if (choiceId === "gate-open-ide" || choiceId === "gate-read-rtl") {
        openIdeFromArtifacts();
        return;
      }
    }

    // RTL clarifier — send the readable choice back to the design agent.
    void sendRtlWithContext(label, { mode: "rtl_designer" });
  }, [
    derivedItems,
    replaceItem,
    beginStagedVerification,
    triggerAttach,
    sendRtlWithContext,
    openIdeFromArtifacts,
    staged,
    openPrimary,
    pushVerificationGate,
    promptForVerificationFiles,
  ]);

  const projectDashboardModel = useMemo(
    () => buildProjectDashboardModel({
      projectId: activeProject?.id,
      projectName: activeProject?.name || "Project",
      artifactBuckets: artifactState?.artifacts || {},
      runs: projectRuns,
      orgMetrics: dashboardMetrics || {},
      tokenUsage: projectTokenUsage || null,
      activeRunId,
    }),
    [activeProject?.id, activeProject?.name, artifactState?.artifacts, projectRuns, dashboardMetrics, projectTokenUsage, activeRunId],
  );

  const resolveEditorTarget = useCallback((payload = {}) => {
    const wantedName = payload.name || payload.filename || payload.file || payload.path || "";
    const normalizedWanted = String(wantedName).replace(/\\/g, "/").toLowerCase();
    const wantedMemberPath = payload.memberPath || payload.relativePath || payload.path || "";
    const normalizedWantedMember = String(wantedMemberPath || "").replace(/\\/g, "/").toLowerCase();
    const artifact = payload.artifactId
      ? flatArtifacts.find((a) => {
          const sameArtifact = a.id === payload.artifactId || a.artifactId === payload.artifactId;
          if (!sameArtifact) return false;
          if (!normalizedWantedMember) return true;
          return String(a.memberPath || a.metadata?.relative_path || "")
            .replace(/\\/g, "/")
            .toLowerCase() === normalizedWantedMember;
        })
      : flatArtifacts.find((a) => {
          const candidates = [
            a.filename,
            a.name,
            a.memberPath,
            a.metadata?.relative_path,
            a.metadata?.path,
          ].filter(Boolean).map((v) => String(v).replace(/\\/g, "/").toLowerCase());
          return candidates.some((candidate) =>
            candidate === normalizedWanted || candidate.endsWith(`/${normalizedWanted}`),
          );
        });

    return {
      artifactId: artifact?.artifactId || payload.artifactId || artifact?.id || null,
      memberPath: wantedMemberPath || artifact?.memberPath || null,
      name: wantedName || artifact?.memberPath || artifact?.filename || artifact?.metadata?.relative_path || "file",
      language: payload.language,
      focusLine: payload.line || payload.focusLine,
    };
  }, [flatArtifacts]);

  const handleOpenFile = useCallback((_itemId, payload) => {
    const target = resolveEditorTarget(payload);
    if (!target.artifactId) {
      pushItem(makeItem(THREAD_KINDS.SAID, {
        text: `I couldn't find "${target.name}" in the current project files yet. Refresh artifacts or open the IDE to inspect the project.`,
      }));
      return;
    }
    openFile(target);
  }, [pushItem, resolveEditorTarget, openFile]);

  const loadFile = useCallback(async (target) => {
    if (!target) return "";
    if (target.memberPath) {
      return onFetchArtifactContent?.(target.artifactId, { memberPath: target.memberPath }) || "";
    }
    return onFetchArtifactContent?.(target.artifactId) || "";
  }, [onFetchArtifactContent]);

  const loadFileBlob = useCallback(async (target) => {
    if (!target?.artifactId) return null;
    return onFetchArtifactBlob?.(target.artifactId) || null;
  }, [onFetchArtifactBlob]);

  const saveFile = useCallback(async (target) => {
    if (!target) return;
    if (target.memberPath) {
      await onSaveRtlProjectMember?.({
        artifactId: target.artifactId,
        memberPath: target.memberPath,
        content: target.content || "",
      });
      return;
    }
    if (activeProject?.id && target.artifactId) {
      const result = await updateProjectArtifactContent(
        activeProject.id,
        target.artifactId,
        target.content || "",
        authToken,
      );
      const updated = result?.artifact;
      if (updated?.id) {
        openFile({
          ...target,
          artifactId: updated.id,
          name: updated.filename || target.name,
          filename: updated.filename || target.filename,
        });
      }
      await onRefreshArtifacts?.(activeProject.id, { silent: true });
    }
  }, [activeProject?.id, authToken, onRefreshArtifacts, onSaveRtlProjectMember, openFile]);

  // ─────────────────────────────────────────────────────────────────────
  // IDE files (active project artifacts)
  // ─────────────────────────────────────────────────────────────────────
  const [ideFiles, setIdeFiles] = useState([]);
  const [ideActiveFileId, setIdeActiveFileId] = useState(null);
  const [ideFileRevisions, setIdeFileRevisions] = useState({});
  const [completionDebug, setCompletionDebug] = useState(null);
  const completionDebugRef = useRef(null);
  const ideActiveFileIdRef = useRef(ideActiveFileId);
  const ideFilesRef = useRef(ideFiles);
  const ideFileRevisionsRef = useRef(ideFileRevisions);

  if (!completionDebugRef.current) {
    completionDebugRef.current = createCompletionDebug({ onChange: setCompletionDebug });
  }

  useEffect(() => {
    ideActiveFileIdRef.current = ideActiveFileId;
  }, [ideActiveFileId]);

  useEffect(() => {
    ideFilesRef.current = ideFiles;
  }, [ideFiles]);

  useEffect(() => {
    ideFileRevisionsRef.current = ideFileRevisions;
  }, [ideFileRevisions]);
  const [ideExplorerBusy, setIdeExplorerBusy] = useState(false);
  const [ideExplorerError, setIdeExplorerError] = useState("");

  useEffect(() => {
    if (!ideOpen) return;
    const next = flatArtifacts.map((a) => {
      const name = a.memberPath || a.filename || a.metadata?.relative_path || "file";
      return {
        id: a.id,
        name,
        path: a.memberPath || a.metadata?.relative_path || a.filename,
        memberPath: a.memberPath || null,
        artifactId: a.artifactId || a.id,
        artifactType: a.artifact_type || null,
        kind: getEditorFileKind(name),
        language: inferMonacoLanguage(name),
        content: "",
        pdfBlob: null,
        loaded: false,
      };
    });
    setIdeFiles(next);
    setIdeActiveFileId((current) => {
      if (current && next.some((file) => file.id === current)) return current;
      return next[0]?.id || null;
    });
  }, [ideOpen, flatArtifacts]);

  useEffect(() => {
    if (!ideOpen || !ideActiveFileId) return;
    const file = ideFiles.find((f) => f.id === ideActiveFileId);
    if (!file || file.loaded) return;
    let cancelled = false;

    const load = async () => {
      try {
        if (file.kind === "pdf") {
          const [blob, text] = await Promise.all([
            onFetchArtifactBlob?.(file.artifactId),
            onFetchArtifactContent?.(file.artifactId),
          ]);
          if (cancelled) return;
          setIdeFiles((prev) => prev.map((f) => (
            f.id === file.id
              ? { ...f, pdfBlob: blob || null, content: text || "", loaded: true }
              : f
          )));
          return;
        }
        const text = await onFetchArtifactContent?.(file.artifactId, {
          memberPath: file.memberPath || undefined,
        });
        const formatted = await formatEditorContent(text || "", file.name || "");
        if (cancelled) return;
        setIdeFiles((prev) => prev.map((f) => (
          f.id === file.id ? { ...f, content: formatted || "", loaded: true } : f
        )));
      } catch {
        if (!cancelled) {
          setIdeFiles((prev) => prev.map((f) => (
            f.id === file.id ? { ...f, loaded: true } : f
          )));
        }
      }
    };

    void load();
    return () => { cancelled = true; };
  }, [ideOpen, ideActiveFileId, ideFiles, onFetchArtifactContent, onFetchArtifactBlob]);

  const handleIdeChange = useCallback((fid, value) => {
    setIdeFiles((prev) => prev.map((f) => f.id === fid ? { ...f, content: value } : f));
    setIdeFileRevisions((prev) => ({
      ...prev,
      [fid]: (prev[fid] || 0) + 1,
    }));
  }, []);

  const handleIdeSave = useCallback(async (file) => {
    if (!file) return;
    if (file.memberPath) {
      await onSaveRtlProjectMember?.({
        artifactId: file.artifactId,
        memberPath: file.memberPath,
        content: file.content || "",
      });
      return;
    }
    if (activeProject?.id && file.artifactId) {
      const result = await updateProjectArtifactContent(
        activeProject.id,
        file.artifactId,
        file.content || "",
        authToken,
      );
      const updated = result?.artifact;
      if (updated?.id) {
        setIdeFiles((prev) => prev.map((f) => (
          f.id === file.id
            ? {
                ...f,
                id: updated.id,
                artifactId: updated.id,
                name: updated.filename || f.name,
                path: updated.metadata?.relative_path || f.path,
              }
            : f
        )));
        setIdeActiveFileId(updated.id);
      }
      await onRefreshArtifacts?.(activeProject.id, { silent: true });
    }
  }, [activeProject?.id, authToken, onRefreshArtifacts, onSaveRtlProjectMember]);

  const handleIdeUploadFiles = useCallback(async (fileList) => {
    const list = Array.from(fileList || []);
    if (!list.length || !activeProject?.id) return;
    setIdeExplorerError("");
    setIdeExplorerBusy(true);
    try {
      await uploadArtifactFiles(list, inferUploadKind(list), "ide_explorer");
      await onRefreshArtifacts?.(activeProject.id, { silent: true });
    } catch (err) {
      setIdeExplorerError(uploadErrorMessage(err));
    } finally {
      setIdeExplorerBusy(false);
    }
  }, [activeProject?.id, uploadArtifactFiles, inferUploadKind, onRefreshArtifacts]);

  const handleIdeRenameFile = useCallback(async (file, newName) => {
    if (!activeProject?.id || !file?.artifactId) return;
    if (file.memberPath) return;
    setIdeExplorerError("");
    setIdeExplorerBusy(true);
    try {
      await renameProjectArtifact(activeProject.id, file.artifactId, newName, authToken);
      await onRefreshArtifacts?.(activeProject.id, { silent: true });
    } catch (err) {
      setIdeExplorerError(err?.message || "Rename failed");
    } finally {
      setIdeExplorerBusy(false);
    }
  }, [activeProject?.id, authToken, onRefreshArtifacts]);

  const handleIdeDeleteFile = useCallback(async (file) => {
    if (!activeProject?.id || !file?.artifactId) return;
    if (file.memberPath) return;
    setIdeExplorerError("");
    setIdeExplorerBusy(true);
    try {
      await deleteProjectArtifact(activeProject.id, file.artifactId, authToken);
      if (ideActiveFileId === file.id) {
        setIdeActiveFileId(null);
      }
      await onRefreshArtifacts?.(activeProject.id, { silent: true });
    } catch (err) {
      setIdeExplorerError(err?.message || "Delete failed");
    } finally {
      setIdeExplorerBusy(false);
    }
  }, [activeProject?.id, authToken, ideActiveFileId, onRefreshArtifacts]);

  const ideInlineCompletion = useMemo(() => {
    if (!activeProject?.id) return null;
    return {
      projectId: activeProject.id,
      authToken,
      enabled: true,
      rtlArtifactId: artifactState?.active?.active_rtl_artifact_id || null,
      specArtifactId: artifactState?.active?.active_spec_artifact_id || null,
      debug: completionDebugRef.current,
      fetchTimeoutMs: 18000,
      getActiveFile: () => (
        ideFilesRef.current.find((f) => f.id === ideActiveFileIdRef.current) || null
      ),
      getIdeFiles: () => ideFilesRef.current,
      getRevisionMap: () => ideFileRevisionsRef.current,
    };
  }, [
    activeProject?.id,
    authToken,
    artifactState?.active?.active_rtl_artifact_id,
    artifactState?.active?.active_spec_artifact_id,
  ]);

  const ideLintOptions = useMemo(() => {
    if (!activeProject?.id) return null;
    return {
      projectId: activeProject.id,
      authToken,
      enabled: true,
    };
  }, [activeProject?.id, authToken]);

  const ideAssistantMessages = useMemo(
    () => ideCompanion.ideMessages.map((m) => ({
      role: m.role,
      text: stripIdeChatDisplay(m.text),
      tokenUsage: m.tokenUsage || null,
    })).filter((m) => m.text),
    [ideCompanion.ideMessages],
  );

  const mainThreadTitle = useMemo(
    () => (chatThreads.threads || []).find((t) => t.id === chatThreads.activeThreadId)?.title
      || "Project Chat",
    [chatThreads.threads, chatThreads.activeThreadId],
  );

  const idePendingAttachmentChips = useMemo(
    () => ideAttachments.map((r) => ({
      key: attachmentRowKey(r),
      label: r.label || r.artifactId || "file",
    })),
    [ideAttachments],
  );

  const ideAttachExistingKeys = useMemo(
    () => ideAttachments.map((r) => attachmentRowKey(r)),
    [ideAttachments],
  );

  const addIdeAttachment = useCallback((row) => {
    if (!row?.artifactId) return;
    setIdeAttachments((prev) => {
      const key = attachmentRowKey(row);
      const map = new Map();
      prev.forEach((r) => map.set(attachmentRowKey(r), r));
      map.set(key, row);
      return Array.from(map.values()).slice(0, MAX_ATTACH);
    });
  }, []);

  const handleIdeAttachConfirm = useCallback((rows) => {
    setIdeAttachments((prev) => {
      const map = new Map();
      prev.forEach((r) => map.set(attachmentRowKey(r), r));
      rows.forEach((r) => map.set(attachmentRowKey(r), r));
      return Array.from(map.values()).slice(0, MAX_ATTACH);
    });
    setIdeAttachModalOpen(false);
  }, []);

  const appendIdePanelMessage = useCallback((role, text) => {
    const line = String(text || "").trim();
    if (!line) return;
    ideCompanion.setIdeMessages((prev) => [...prev, { role, text: line }]);
  }, [ideCompanion.setIdeMessages]);

  const handleIdeAskAssistant = useCallback(async (text, activeFile) => {
    const trimmed = String(text || "").trim();
    const fallback = "I've attached files for context — use the open file and main thread memory.";
    const message = trimmed || (ideAttachments.length ? fallback : "");
    if (!message) return;
    if (!activeProject?.id) {
      appendIdePanelMessage("assistant", "Pick a project first, then ask from the IDE.");
      return;
    }
    if (!chatThreads.activeThreadId) {
      appendIdePanelMessage(
        "assistant",
        "Start or select a main conversation thread first. The IDE agent links to it for context only.",
      );
      return;
    }

    let tid = ideCompanion.ideThreadId;
    if (!tid) {
      try {
        tid = await ideCompanion.ensureCompanion();
      } catch {
        appendIdePanelMessage("assistant", "Could not open the IDE assistant thread. Please retry.");
        return;
      }
    }
    if (!tid) {
      appendIdePanelMessage("assistant", "IDE assistant thread is not ready yet.");
      return;
    }

    const ctx = {};
    if (activeFile) {
      ctx.active_file_id = activeFile.artifactId || null;
      ctx.active_file_path = activeFile.path || activeFile.name || null;
      ctx.active_file_name = activeFile.name || null;
      const buf = activeFile.content;
      if (buf != null && String(buf).trim()) {
        ctx.active_file_content = String(buf).slice(0, 32000);
      }
    }

    const displayUser = trimmed
      || (ideAttachments.length
        ? `Attached ${ideAttachments.length} file${ideAttachments.length === 1 ? "" : "s"} for context`
        : "");
    if (displayUser) {
      ideCompanion.setIdeMessages((prev) => [...prev, { role: "user", text: displayUser }]);
    }

    await sendIdeWithContext(message, {
      context: ctx,
      attachments: ideAttachments,
    });
  }, [
    activeProject?.id,
    chatThreads.activeThreadId,
    ideAttachments,
    ideCompanion,
    sendIdeWithContext,
    appendIdePanelMessage,
  ]);

  const onboarding = useChipixOnboarding({
    enabled: Boolean(activeProject?.id) && !rtlStreaming,
    isDarkTheme,
    projectId: activeProject?.id,
  });

  // ─────────────────────────────────────────────────────────────────────
  // Palette
  // ─────────────────────────────────────────────────────────────────────
  const paletteSections = useMemo(() => {
    const baseActions = [
      { id: "action:newproj", name: "Start a new project", meta: "⌘N", kind: "new-project", glyph: "+" },
      { id: "action:newchat", name: "Start a new conversation", meta: "", kind: "new-chat", glyph: "✎" },
      { id: "action:ide", name: "Open IDE workspace", meta: "⌘⇧E", kind: "ide", glyph: "</>" },
      { id: "action:mental", name: "Show mental model", meta: "", kind: "mental", glyph: "Ψ" },
      { id: "action:verify", name: "Start verification on active design", meta: "", kind: "verify", glyph: "▶" },
      { id: "action:staged-verify", name: "Plan then verify (staged)", meta: "", kind: "staged-verify", glyph: "✓" },
      { id: "action:dashboard", name: "Project dashboard", hint: "Runs, files, trends", kind: "dashboard", glyph: "▦" },
      { id: "action:docs", name: "Open documentation", meta: "", kind: "docs", glyph: "📖" },
      { id: "action:tour", name: "Take the workspace tour", meta: "Guide", kind: "restart-tour", glyph: "◎" },
    ];
    const sections = buildPaletteSections({
      projects,
      files: flatArtifacts,
      runs: projectRuns,
      actions: baseActions,
    });
    // Add a Conversations section (only when there's >1 thread — single-thread
    // users never see thread-switching chrome).
    const otherThreads = (chatThreads.threads || []).filter((t) => t.id !== chatThreads.activeThreadId);
    if (otherThreads.length) {
      sections.splice(1, 0, {
        label: "Switch conversation",
        glyph: "≡",
        items: otherThreads.slice(0, 20).map((t) => ({
          id: `thread:${t.id}`,
          name: t.agent_name || t.title || "Untitled conversation",
          meta: t.active_task?.display_id
            ? `${t.active_task.display_id} · ${t.active_task.status || ""}`
            : (t.archived ? "archived" : ""),
          ref: t,
          kind: "switch-chat",
        })),
      });
    }
    return sections;
  }, [projects, flatArtifacts, projectRuns, chatThreads.threads, chatThreads.activeThreadId]);

  const handlePalettePick = useCallback((item) => {
    if (item.kind === "file" && item.ref) {
      openFile({
        artifactId: item.ref.artifactId || item.ref.id,
        memberPath: item.ref.memberPath || undefined,
        name: item.ref.memberPath || item.ref.filename || item.ref.metadata?.relative_path,
      });
    } else if (item.kind === "project" && item.ref) {
      onSelectProject?.(item.ref.id);
    } else if (item.kind === "run" && item.ref) {
      const rid = item.ref.id || item.ref.run_id;
      if (rid) onSelectActiveRun?.(rid);
    } else if (item.kind === "ide") {
      openPrimary("ide");
    } else if (item.kind === "mental") {
      openPrimary("mental");
    } else if (item.kind === "new-project") {
      onCreateProject?.();
    } else if (item.kind === "verify") {
      beginStagedVerification();
    } else if (item.kind === "staged-verify") {
      void staged.prepareThenReviewMentalModel("design-gate");
    } else if (item.kind === "dashboard") {
      openProjectDashboard();
    } else if (item.kind === "docs") {
      openPrimary("docs");
    } else if (item.kind === "new-chat") {
      void chatThreads.createThread({ title: "Project Chat", switchTo: true })
        .then(() => setHasInteracted(false));
    } else if (item.kind === "switch-chat" && item.ref?.id) {
      void chatThreads.switchThread(item.ref.id);
    } else if (item.kind === "restart-tour") {
      onboarding.restartTour();
    }
  }, [onSelectProject, onSelectActiveRun, onCreateProject, beginStagedVerification, staged, chatThreads, openProjectDashboard, openPrimary, openFile, onboarding]);

  const dashboardAgentSendOpts = useMemo(() => {
    const snap = buildDashboardVerificationSnapshot(
      mergedDashboardMetrics,
      activeRunStatus,
      activeRunId,
      null,
    );
    if (!snap) return {};
    return { context: { verification_turn_snapshot_json: snap } };
  }, [mergedDashboardMetrics, activeRunStatus, activeRunId]);

  // ─────────────────────────────────────────────────────────────────────
  // Composer suggestions (context-aware)
  // ─────────────────────────────────────────────────────────────────────
  const suggestions = useMemo(() => {
    // For a fresh thread that has no conversation yet, never inherit
    // project-state-derived suggestions (completed/failed/running). The
    // composer below the welcome screen is silent — the starters above it
    // own the "what should I do first?" answer.
    if (!hasThreadActivity) return [];

    if (rtlStreaming) {
      return [{ id: "cancel-cadence", label: "Cancel Cadence run" }];
    }

    if (staged.stage === "completed") {
      const chips = [];
      if (cadenceSimulationPending(
        staged.stage,
        cadenceStatus?.tone,
        staged.snapshot?.executionResult,
        stagedVerdictPayload,
      )) {
        chips.push({ id: "run-cadence", label: "Run Cadence simulation", accent: true });
      } else if (stagedVerdictPayload?.cadenceSimulationStatus === "failed") {
        chips.push({ id: "fix-cadence", label: "Fix and re-run Cadence", accent: true });
      }
      if (chips.length) return chips;
    }

    const status = String(activeRunStatus?.status || "").toLowerCase();
    if (status === "failed") {
      return [
        { id: "ask-why", label: "Explain the failure", accent: true },
        { id: "fix", label: "Propose a fix" },
        { id: "rerun", label: "Re-run verification" },
      ];
    }
    if (status === "completed") {
      const chips = [];
      if (cadenceSimulationPending(
        staged.stage,
        cadenceStatus?.tone,
        staged.snapshot?.executionResult,
        stagedVerdictPayload,
      )) {
        chips.push({ id: "run-cadence", label: "Run Cadence simulation", accent: true });
      } else if (stagedVerdictPayload?.cadenceSimulationStatus === "failed") {
        chips.push({ id: "fix-cadence", label: "Fix and re-run Cadence", accent: true });
      }
      chips.push(
        { id: "promote", label: "Promote design", accent: chips.length === 0 },
        { id: "compare", label: "Compare with previous" },
        { id: "coverage", label: "Show coverage" },
      );
      return chips;
    }
    if (status === "running") {
      return [
        { id: "cancel", label: "Cancel run" },
      ];
    }
    if (designLifecycle === "design_complete") {
      return [
        { id: "gate-build-mental-model", label: "Build mental model", accent: true },
        { id: "gate-open-ide", label: "Open in editor" },
      ];
    }
    if (designLifecycle === "mm_done") {
      return [
        { id: "gate-start-verification", label: "Start verification", accent: true },
        { id: "ide", label: "Open IDE" },
        { id: "mental", label: "Review mental model" },
      ];
    }
    // Mode-aware idle suggestions — lens biases what we surface.
    if (chipixMode.mode === CHIPIX_MODES.VERIFY) {
      const verifyChips = [];
      if (chipixMode.derivedPhase === "mental-model") {
        if (projectHasMentalModel) {
          verifyChips.push({ id: "mental", label: "Show mental model", accent: true });
        } else {
          verifyChips.push({ id: "gate-build-mental-model", label: "Build mental model", accent: true });
        }
        verifyChips.push({ id: "gate-open-ide", label: "Open IDE" });
        return verifyChips;
      }
      if (chipixMode.derivedPhase === "ready-to-verify") {
        verifyChips.push({ id: "gate-start-verification", label: "Plan and run verification", accent: true });
      }
      if (projectHasRuns) verifyChips.push({ id: "dashboard", label: "Open dashboard" });
      verifyChips.push({ id: "ide", label: "Open IDE" });
      return verifyChips;
    }
    // Design mode default
    const designChips = [];
    if (flatArtifacts.length > 0 && projectHasMentalModel) {
      designChips.push({ id: "mental", label: "Show mental model" });
    }
    designChips.push({ id: "ide", label: "Open IDE" });
    return designChips;
  }, [activeRunStatus?.status, hasThreadActivity, designLifecycle, chipixMode.mode, chipixMode.derivedPhase, projectHasRuns, flatArtifacts.length, projectHasMentalModel, rtlStreaming, staged.stage, staged.snapshot?.executionResult, stagedVerdictPayload, cadenceStatus?.tone]);

  const handleSuggestion = useCallback((s) => {
    if (s.id === "gate-build-mental-model") {
      void staged.prepareThenReviewMentalModel("design-gate");
    } else if (s.id === "gate-open-ide") {
      openIdeFromArtifacts();
    } else if (s.id === "gate-start-verification") {
      setHasInteracted(true);
      void staged.recommend();
    } else if (s.id === "staged") {
      void staged.prepareThenReviewMentalModel("design-gate");
    } else if (s.id === "verify") {
      setHasInteracted(true);
      if (!designGateRef.current.mentalModelDone) {
        void staged.prepareThenReviewMentalModel("design-gate");
      } else {
        void staged.recommend();
      }
    } else if (s.id === "mental") {
      openPrimary("mental");
    } else if (s.id === "dashboard") {
      openProjectDashboard();
    } else if (s.id === "ide") {
      openIdeFromArtifacts();
    } else if (s.id === "cancel" && activeRunId) {
      void onCancelRun?.(activeRunId);
    } else if (s.id === "cancel-cadence") {
      cancelRtlStream();
    } else if (s.id === "run-cadence") {
      triggerCadenceVerification();
    } else if (s.id === "fix-cadence") {
      triggerCadenceVerification(
        "Cadence simulation failed. Diagnose the failure, apply fixes to the generated UVM collateral, and re-run Cadence simulation.",
        {},
        { useAgent: true },
      );
    } else if (s.id === "rerun") {
      void startRunForActiveThread({ specType: "text", prompt: "Re-run verification on the latest changes." });
    } else if (s.id === "ask-why") {
      void handleSend("Why did the last verification fail? Show me the failing scenario in plain English.", dashboardAgentSendOpts);
    } else if (s.id === "fix") {
      void handleSend("Propose a minimal fix for the last failure and show it as a diff.", dashboardAgentSendOpts);
    } else if (s.id === "promote") {
      void handleSend("Promote the current design as the active baseline.", dashboardAgentSendOpts);
    } else if (s.id === "compare") {
      void handleSend("Compare the current run with the previous run and summarise differences.", dashboardAgentSendOpts);
    } else if (s.id === "coverage") {
      void handleSend("Summarise the current functional and code coverage.", dashboardAgentSendOpts);
    }
  }, [startRunForActiveThread, onCancelRun, activeRunId, handleSend, staged, openIdeFromArtifacts, dashboardAgentSendOpts, openProjectDashboard, cancelRtlStream, triggerCadenceVerification]);

  // ─────────────────────────────────────────────────────────────────────
  // Card handlers wiring
  // ─────────────────────────────────────────────────────────────────────
  // ── Patch apply / reject from DiffCards in the thread ─────────────────
  const handleApplyPatch = useCallback(async (itemId, patchId) => {
    try {
      await patchInbox.approve(patchId);
      pushItem(makeItem(THREAD_KINDS.SAID, { text: "Applied. Re-running…" }));
      void startRunForActiveThread({ specType: "text", prompt: "Re-run verification after applying the patch." });
    } catch (err) {
      pushItem(makeItem(THREAD_KINDS.SAID, {
        text: `Couldn't apply patch: ${err?.message || "unknown error"}.`,
      }));
    }
  }, [patchInbox, pushItem, startRunForActiveThread]);

  const handleRejectPatch = useCallback(async (itemId, patchId, _payload, reason) => {
    try {
      await patchInbox.reject(patchId, reason || "Rejected by user");
      // Echo the user's reason as a user bubble so the agent has it in
      // explicit context for its next attempt; without it the agent only
      // sees the bare "rejected" signal from the backend.
      if (reason && reason.trim()) {
        pushItem(makeItem(THREAD_KINDS.USER, {
          text: `Rejected the proposed fix — reason: ${reason.trim()}`,
        }));
        // Trigger an agent turn that takes the rejection reason as input.
        void sendRtlWithContext(
          `I rejected the previous fix. Reason: ${reason.trim()}. Please propose a different approach.`,
        );
      } else {
        pushItem(makeItem(THREAD_KINDS.SAID, {
          text: "Rejected. I'll come back with another approach.",
        }));
      }
    } catch (err) {
      pushItem(makeItem(THREAD_KINDS.SAID, {
        text: `Couldn't reject patch: ${err?.message || "unknown error"}.`,
      }));
    }
  }, [patchInbox, pushItem, sendRtlWithContext]);

  const handlers = useMemo(() => ({
    onEditUserMessage: handleEditUserMessage,
    onAskChoose: handleAskChoose,
    onOpenFile: handleOpenFile,
    onRunCancel: onCancelRun,
    onRunDownload: onDownloadRun,
    onRunUploadLog: (_id, payload) => {
      triggerUvmLogUpload({
        runId: payload?.runId || payload?.run_id || null,
        generatedArtifactIds: payload?.generatedArtifactIds?.length
          ? payload.generatedArtifactIds
          : payload?.downloadArtifactIds || [],
      });
    },
    onOpenMentalModel: () => openPrimary("mental"),
    onOpenCodebaseGraph: () => openPrimary("codebaseGraph"),
    onOpenSchematic: () => openPrimary("mental"),
    onOpenDashboard: () => openProjectDashboard(),
    onApplyPatch: handleApplyPatch,
    onRejectPatch: handleRejectPatch,
    onDiffEdit: (_id, payload) => handleOpenFile(_id, {
      name: payload.filename,
      file: payload.filename,
      line: payload.line,
    }),
    onProposeFix: () => handleSend(
      "Propose a minimal fix for the failure I just saw, as a diff.",
      dashboardAgentSendOpts,
    ),
    onMentalModelApprove: (itemId) => {
      const item = derivedItems.find((it) => it.id === itemId);
      const source = item?.payload?._source;
      replaceItem(itemId, (cur) => ({ payload: { ...cur.payload, approved: true } }));
      if (source === "design-gate" || source === "imported" || source === "overlay-build") {
        designGateRef.current.mentalModelDone = true;
        setDesignLifecycle("mm_done");
        pushVerificationGate();
      } else if (source === "staged") {
        // The mental-model checkpoint inside the staged flow — approval here
        // unlocks the strategy-recommendation step rather than asking the
        // RTL agent to "continue with the plan".
        void staged.recommend();
      } else {
        void sendRtlWithContext("Looks right — please continue with the plan.");
      }
    },
    onMentalModelCorrect: (itemId, text) => {
      const item = derivedItems.find((it) => it.id === itemId);
      const source = item?.payload?._source;
      replaceItem(itemId, (cur) => ({ payload: { ...cur.payload, superseded: true } }));
      if (source === "design-gate" || source === "imported" || source === "staged") {
        pushItem(makeItem(THREAD_KINDS.USER, { text: `Correct the mental model: ${text}` }));
        designGateRef.current.mentalModelDone = false;
        designGateRef.current.verifyAskShown = false;
        setDesignLifecycle(source === "imported" ? "idle" : "design_complete");
        void staged.prepareThenReviewMentalModel(
          source === "staged" ? "staged" : source === "imported" ? "imported" : "design-gate",
        );
      } else {
        void sendRtlWithContext(`Correct the mental model: ${text}`);
      }
    },
    onStagedRecommend: () => staged.reviewMentalModel(),
    onStagedPickStrategy: (_id, strategyId) => staged.plan(strategyId),
    onPlanAction: (itemId, actionId, extra) => {
      const item = derivedItems.find((it) => it.id === itemId);
      const source = item?.payload?._source;
      if (source === "rtl-design") {
        const planId = item?.payload?.planId || itemId;
        if (actionId === "approve") {
          track(AnalyticsEvents.PLAN_IMPLEMENT_CLICKED, {
            project_id: activeProject?.id,
            thread_id: chatThreads.activeThreadId,
            source: "rtl-design",
            plan_item_id: planId,
          });
          replaceItem(itemId, (cur) => ({
            payload: { ...cur.payload, superseded: true, actions: [] },
          }));
          planGateRef.current.requirePlanApproval = false;
          planGateRef.current.activePlanItemId = null;
          clearImplementationPlanState(activeProject?.id, chatThreads.activeThreadId);
          void sendRtlWithContext(
            "Implement the approved implementation plan. Execute every deliverable listed in the plan; do not ask clarifying questions again.",
            {
              mode: "rtl_designer",
              context: {
                plan_approved: true,
                implement_plan: true,
                require_plan_approval: false,
                mode: "rtl_designer",
                plan_id: planId,
              },
            },
          );
        } else if (actionId === "refine") {
          track(AnalyticsEvents.PLAN_IMPROVE_CLICKED, {
            project_id: activeProject?.id,
            thread_id: chatThreads.activeThreadId,
            source: "rtl-design",
            plan_item_id: planId,
          });
          replaceItem(itemId, (cur) => ({
            payload: { ...cur.payload, superseded: true, actions: [] },
          }));
          planGateRef.current.requirePlanApproval = true;
          void sendRtlWithContext(
            `Improve the implementation plan: ${extra}. Emit an updated single chipix:plan fence.`,
            {
              mode: "rtl_designer",
              context: {
                require_plan_approval: true,
                plan_refine: true,
                mode: "rtl_designer",
              },
            },
          );
        }
        return;
      }
      if (actionId === "openPlanMarkdown") {
        const doc = item?.payload?.planDocument || extra;
        if (doc?.artifactId) {
          handleOpenFile(itemId, {
            artifactId: doc.artifactId,
            name: doc.name || doc.filename || "verification_plan.md",
            filename: doc.filename || doc.name || "verification_plan.md",
            language: "markdown",
          });
        } else {
          openIdeFromArtifacts();
        }
        return;
      }
      if (actionId === "approve") {
        track(AnalyticsEvents.PLAN_IMPLEMENT_CLICKED, {
          project_id: activeProject?.id,
          source: "staged-verification",
        });
        void staged.approveAndExecute();
      } else if (actionId === "refine") {
        track(AnalyticsEvents.PLAN_IMPROVE_CLICKED, {
          project_id: activeProject?.id,
          source: "staged-verification",
        });
        void staged.refine(extra);
      } else if (actionId === "toggle-run-cadence") {
        staged.setRunCadenceSimulation(Boolean(extra));
      }
    },
    onCadenceRunCancel: () => cancelRtlStream(),
    cadenceConnected: cadenceStatus?.tone === "ok",
    onVerdictAction: (_id, actionId, payload) => {
      const snap = buildVerificationTurnSnapshot(payload);
      const cadenceCtx = buildCadenceVerificationContext({
        projectId: activeProject?.id,
        executionResult: staged.snapshot?.executionResult,
        verdictPayload: payload,
      });
      const agentCtx = {
        ...(snap ? { verification_turn_snapshot_json: snap } : {}),
        ...cadenceCtx,
      };
      const sendOpts = { context: agentCtx, allowAgentVerificationStart: true };
      if (actionId === "rerun") void startRunForActiveThread({ specType: "text", prompt: "Re-run the latest verification." });
      else if (actionId === "upload-log") {
        triggerUvmLogUpload(payload);
      }
      else if (actionId === "explain") {
        void handleSend("Explain why the verification failed in plain English.", sendOpts);
      } else if (actionId === "promote") {
        void handleSend("Promote the current design as the active baseline.", sendOpts);
      } else if (actionId === "run-cadence" || actionId === "rerun-cadence") {
        triggerCadenceVerification(undefined, cadenceCtx);
      } else if (actionId === "fix-cadence") {
        triggerCadenceVerification(
          "Cadence simulation failed. Diagnose the failure, apply fixes to the generated UVM collateral, and re-run Cadence simulation.",
          cadenceCtx,
          { useAgent: true },
        );
      } else if (actionId === "open-dashboard") openProjectDashboard();
    },
  }), [
    handleEditUserMessage,
    handleAskChoose,
    handleOpenFile,
    onCancelRun,
    onDownloadRun,
    startRunForActiveThread,
    handleApplyPatch,
    handleRejectPatch,
    handleSend,
    staged,
    derivedItems,
    replaceItem,
    sendRtlWithContext,
    pushVerificationGate,
    dashboardAgentSendOpts,
    openIdeFromArtifacts,
    openProjectDashboard,
    activeProject?.id,
    chatThreads.activeThreadId,
    triggerUvmLogUpload,
    triggerCadenceVerification,
    cancelRtlStream,
    cadenceStatus,
  ]);

  // Pending patches list for the drawer.
  const pendingPatches = useMemo(
    () => (patchInbox.patches || []).filter((p) => p.status === "awaiting_approval" || p.status === "pending"),
    [patchInbox.patches],
  );

  const scrollToPatch = useCallback((patchId) => {
    const threadItemId = patchToThreadIdRef.current.get(patchId);
    if (!threadItemId) return;
    const el = document.querySelector(`[data-thread-item="${threadItemId}"]`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  const recentDiff = useMemo(() => {
    const it = [...derivedItems].reverse().find((x) => x?.kind === THREAD_KINDS.DIFF);
    if (!it) return null;
    const meta = it.payload || {};
    return {
      title: meta.title || meta.filename || meta.file || "Proposed change",
      summary: meta.summary || meta.subtitle || "",
      lines: meta.lines || meta.diff || [],
      itemId: it.id,
    };
  }, [derivedItems]);

  const openRecentDiffInThread = useCallback(() => {
    closeAll();
    const threadItemId = recentDiff?.itemId;
    if (!threadItemId) return;
    requestAnimationFrame(() => {
      const el = document.querySelector(`[data-thread-item="${threadItemId}"]`);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }, [recentDiff?.itemId, closeAll]);

  const recentArtifacts = useMemo(() => flatArtifacts.slice(0, 30), [flatArtifacts]);

  // Pick the right starter set based on project state. From the philosophy
  // doc: "no empty states — context-aware suggestions, not 'select a file to
  // begin'." A project with files but no runs should suggest verification,
  // not another design. A project mid-flight should suggest iteration.
  const adaptiveStarters = useMemo(() => {
    // Explicit "Start a new design" trumps project state — the user has
    // told us they want a fresh design conversation, so offer concrete
    // greenfield options rather than re-showing the iterate set.
    if (forceGreenfieldStarters) return GREENFIELD_STARTERS;
    const fileCount = flatArtifacts.length;
    const runCount = activeProject?.id
      ? projectRuns.filter((r) => String(r.project_id || "") === String(activeProject.id)).length
      : 0;

    // Verify-mode lens: bias suggestions toward "what to check?"
    if (chipixMode.mode === CHIPIX_MODES.VERIFY) {
      if (runCount > 0) return ITERATING_STARTERS;
      if (fileCount > 0) return VERIFY_STARTERS_HAS_FILES;
      return VERIFY_STARTERS_EMPTY;
    }

    // Design-mode lens (default): bias toward "what to build / refine?"
    if (runCount > 0) return ITERATING_STARTERS;
    if (fileCount > 0) return HAS_FILES_STARTERS;
    return GREENFIELD_STARTERS;
  }, [flatArtifacts.length, projectRuns, activeProject?.id, forceGreenfieldStarters, chipixMode.mode]);

  const welcomeCopy = useMemo(() => {
    const fileCount = flatArtifacts.length;
    const runCount = Array.isArray(projectRuns) ? projectRuns.length : 0;
    const isVerify = chipixMode.mode === CHIPIX_MODES.VERIFY;

    if (runCount > 0) return isVerify ? {
      head: "Let's verify — again.",
      body: "Pick up where you left off — re-run, open the dashboard, or zero in on what failed last time.",
    } : {
      head: "Where do we go from here?",
      body: "Pick up the conversation — refine the design, re-verify, or inspect the last run.",
    };

    if (fileCount > 0) return isVerify ? {
      head: "Let's verify your design.",
      body: designLifecycle === "mm_done"
        ? "Mental model confirmed — say the word to plan strategies and run checks."
        : "First I'll build a mental model from your spec and RTL (a plain-English map of the design), then we plan and run verification.",
    } : {
      head: "Your project is loaded.",
      body: `${fileCount} file${fileCount === 1 ? "" : "s"} attached. What would you like to do first?`,
    };

    return isVerify ? {
      head: "Let's verify.",
      body: "Bring me RTL — files you already have, or pick a template and we'll design plus verify in one pass.",
    } : {
      head: "Let's design something.",
      body: "Describe a chip in plain English. I'll show you how I understand it, write the RTL, then we move to verification together.",
    };
  }, [flatArtifacts.length, projectRuns, chipixMode.mode, designLifecycle]);

  const showWelcome = derivedItems.length === 0 && !rtlStreaming;

  // ── Desktop chrome bindings ──────────────────────────────────────────
  const stagedRailActive = !titleBarOverlay
    && ["preparing", "recommended", "planned", "refining", "executing"].includes(staged.stage);
  const sideRailHighlight = drawerOpen ? "threads" : sideRailActive || (stagedRailActive ? "staged" : null);

  const handleSideRailSelect = useCallback((id) => {
    switch (id) {
      case "threads":
        setDrawerOpen((open) => !open);
        break;
      case "ide":
        if (ideOpen) goBack();
        else openIdeFromArtifacts();
        break;
      case "mental":
        togglePrimary("mental");
        break;
      case "codebase":
        togglePrimary("codebaseGraph");
        break;
      case "staged":
        setDrawerOpen(false);
        closeAll();
        if (staged.stage === "planned") {
          void staged.approveAndExecute();
        } else if (staged.stage === "idle" || staged.stage === "completed" || staged.stage === "failed") {
          if (staged.stage === "completed" || staged.stage === "failed") staged.reset();
          beginStagedVerification();
        }
        break;
      case "dash":
        if (dashOpen) goBack();
        else openProjectDashboard();
        break;
      case "tasks":
        if (tasksOpen) goBack();
        else openTaskBoard();
        break;
      case "founders":
        if (foundersOpen) goBack();
        else {
          track(AnalyticsEvents.FOUNDERS_LETTER_OPENED);
          togglePrimary("founders");
        }
        break;
      case "settings":
        setDrawerOpen(true);
        break;
      default:
        break;
    }
  }, [ideOpen, dashOpen, cgOpen, tasksOpen, foundersOpen, beginStagedVerification, closeAll, goBack, openIdeFromArtifacts, openProjectDashboard, openTaskBoard, staged, togglePrimary]);

  const dismissSurface = useCallback(() => {
    titleBarOverlay?.onClose?.();
  }, [titleBarOverlay]);

  const handleResetThread = useCallback(() => {
    clearItems();
    setHasInteracted(false);
    setForceGreenfieldStarters(true);
    void chatThreads.createThread({ title: "New chat", switchTo: true });
  }, [clearItems, chatThreads]);

  // Compose a last-verdict pill for the status bar from the most recent
  // VERDICT card in the timeline.
  const statusVerdict = useMemo(() => {
    const it = [...derivedItems].reverse().find((x) => x?.kind === THREAD_KINDS.VERDICT);
    if (!it) return null;
    const p = it.payload || {};
    return {
      ok: Boolean(p.ok),
      summary: p.title || (p.ok ? "Passed" : "Failed"),
      sub: p.sub || null,
      onOpen: () => openProjectDashboard(),
    };
  }, [derivedItems, openProjectDashboard]);

  const statusActivityText = rtlStreaming
    ? (rtlCurrentFile ? `Generating ${rtlCurrentFile}` : "Working with the agent")
    : null;

  return (
    <>
    <div
      className="tf-app"
      data-ui-shell="thread-first"
      data-tf-theme={isDarkTheme ? "dark" : "light"}
      data-tf-mode={chipixMode.mode}
    >
      <ChipixSideRail
        active={sideRailHighlight}
        isDarkTheme={isDarkTheme}
        onSelect={handleSideRailSelect}
        onToggleTheme={onToggleTheme}
        onOpenProjectSwitch={() => setPaletteOpen(true)}
      />

      <ChipixTitleBar
        projects={projects}
        activeProject={activeProject}
        onSelectProject={onSelectProject}
        onCreateProject={onCreateProject}
        onResetThread={handleResetThread}
        overlay={titleBarOverlay}
        onOpenPalette={() => setPaletteOpen(true)}
      />

      <div className="tf-thread-wrap" ref={threadScrollRef} data-tour="thread-area">
        <div className="tf-thread">
          {showWelcome ? (
            <div className="tf-welcome" data-tour="welcome">
              <img className="tf-crest" src="/brand/chipix-app-icon.png" alt="Chipix" width={56} height={56} draggable={false} />
              <h1>{welcomeCopy.head}</h1>
              <p>{welcomeCopy.body}</p>
              <div className="tf-starters">
                {adaptiveStarters.map((s) => (
                  <button key={s.id} className="tf-starter" onClick={() => handleStarter(s)}>
                    <div className="lbl">{s.lbl}</div>
                    <div className="txt">{s.txt}</div>
                    <div className="sub">{s.sub}</div>
                  </button>
                ))}
              </div>
              {artifactsLoading ? (
                <div style={{ marginTop: 18, color: "var(--tf-ink-3)", fontSize: 13 }}>
                  Loading project artifacts…
                </div>
              ) : null}
              {!toolchain.loading && !toolchain.dismissed && toolchain.missing.length > 0 ? (
                <div className="tf-toolchain-note" role="status">
                  <span className="tf-toolchain-note-lead">Heads up —</span>
                  <span className="tf-toolchain-note-body">
                    {toolchain.missing
                      .map((m) => `${m.label} isn't available${m.impact ? ` (${m.impact})` : ""}`)
                      .join("; ")}
                    .
                  </span>
                  <button
                    type="button"
                    className="tf-toolchain-note-dismiss"
                    onClick={toolchain.dismiss}
                    aria-label="Dismiss"
                  >
                    ×
                  </button>
                </div>
              ) : null}
            </div>
          ) : (
            <>
              {derivedItems.map((item) => (
                <div key={item.id} data-thread-item={item.id}>
                  <ThreadItemRenderer item={item} handlers={handlers} />
                </div>
              ))}
              {rtlStreaming ? null : null}
            </>
          )}
        </div>
      </div>

      <ThreadComposer
        modeRail={
          rtlStreaming ? null : (
            <ChipixModeToggle
              mode={chipixMode.mode}
              onChange={chipixMode.setMode}
              isGated={chipixMode.isGated}
              gateReason={chipixMode.gateReason}
            />
          )
        }
        value={composer}
        onChange={setComposer}
        onSend={() => handleSend()}
        onUploadSpec={() => triggerAttach(ARTIFACT_KIND.SPEC)}
        onUploadRtl={() => triggerAttach(ARTIFACT_KIND.RTL)}
        onUploadRtlFolder={() => triggerAttach(ARTIFACT_KIND.RTL_FOLDER)}
        onOpenWorkspaceAttach={openWorkspaceAttach}
        attachmentChips={composerAttachmentChips}
        attachStatusLine={composerAttachStatusLine}
        onRemoveAttachment={handleRemoveComposerAttachment}
        suggestions={suggestions}
        onSuggestion={handleSuggestion}
        disabled={rtlStreaming || clarifier.isOpen}
        busy={rtlStreaming}
        onCancel={cancelRtlStream}
        currentFile={rtlCurrentFile}
        liveWrite={rtlLiveWrite}
        streamStartedAt={rtlStreamStartedAt}
        placeholder={composerPlaceholder}
        showHdlLanguagePicker={showHdlLanguagePicker}
        hdlLanguage={designHdlLanguage}
        onHdlLanguageChange={setDesignHdlLanguagePersisted}
        tokenContext={mainComposerTokenContext}
      />

      <ChipixStatusBar
        backendStatus={health}
        backendLabel={healthLabel}
        mode={chipixMode.mode}
        derivedPhase={chipixMode.derivedPhase}
        busy={rtlStreaming}
        activityText={statusActivityText}
        streamStartedAt={rtlStreamStartedAt}
        verdict={statusVerdict}
        projectLabel={activeProject?.name || null}
        cadenceStatus={cadenceStatus.loading ? null : cadenceStatus}
        onConfigureCadence={() => setCadenceConfigOpen(true)}
        onOpenDashboard={openProjectDashboard}
        onOpenPalette={() => setPaletteOpen(true)}
        codebaseGraphStatus={codebaseGraphStatus}
        onOpenCodebaseGraph={() => togglePrimary("codebaseGraph")}
      />

      <CadenceConfigModal
        open={cadenceConfigOpen}
        onClose={() => setCadenceConfigOpen(false)}
        authToken={authToken}
        detail={cadenceStatus.detail}
        onSaved={() => cadenceStatus.refresh()}
      />

      <WorkspaceAttachModal
        open={workspaceAttachOpen}
        onClose={() => setWorkspaceAttachOpen(false)}
        artifacts={flatArtifacts}
        existingKeys={workspaceAttachExistingKeys}
        onConfirm={handleWorkspaceAttachConfirm}
      />

      <WorkspaceAttachModal
        open={ideAttachModalOpen}
        onClose={() => setIdeAttachModalOpen(false)}
        artifacts={flatArtifacts}
        existingKeys={ideAttachExistingKeys}
        onConfirm={handleIdeAttachConfirm}
      />

      <input
        ref={specInputRef}
        type="file"
        multiple
        accept=".pdf,.md,.markdown,.txt,.text,.doc,.docx,.json,.yaml,.yml,.rst,.csv,.log,.html,.htm,.xml"
        style={{ display: "none" }}
        onChange={handleFilesPicked}
      />
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".sv,.svh,.v,.vh,.vhd,.vhdl,.zip"
        style={{ display: "none" }}
        onChange={handleFilesPicked}
      />
      <input
        ref={folderInputRef}
        type="file"
        multiple
        webkitdirectory=""
        directory=""
        style={{ display: "none" }}
        onChange={handleFilesPicked}
      />
      <input
        ref={uvmLogInputRef}
        type="file"
        multiple
        accept=".log,.txt,.out"
        style={{ display: "none" }}
        onChange={handleUvmDebugLogsPicked}
      />

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        sections={paletteSections}
        onPick={handlePalettePick}
      />

      {onboarding.Tour}

      <DashboardOverlay
        open={dashOpen}
        onClose={dismissSurface}
        model={projectDashboardModel}
        onSelectRun={(runId) => {
          if (runId) onSelectActiveRun?.(runId);
          dismissSurface();
          setHasInteracted(true);
        }}
        onOpenIde={() => {
          openPrimary("ide");
        }}
        onStartVerification={() => {
          dismissSurface();
          void startRunForActiveThread({ specType: "text", prompt: "Run full verification on the active design." });
        }}
      />

      <TaskBoardOverlay
        open={tasksOpen}
        onClose={dismissSurface}
        projectName={activeProject?.name}
        tasks={projectTaskList}
        loading={projectTasksLoading}
        syncing={projectTasksSyncing}
        error={projectTasksError}
        agents={projectTaskAgents}
        onMoveTask={moveProjectTask}
        onOpenTask={handleOpenBoardTask}
        onCreateTask={handleCreateBoardTask}
        onRetry={handleRetryProjectTasks}
      />

      <SingleFileEditor
        open={Boolean(editorTarget)}
        target={editorTarget}
        onClose={closeFile}
        onLoad={loadFile}
        onLoadBlob={loadFileBlob}
        onSave={saveFile}
        onAsk={(text) => {
          // Capture the active file context BEFORE the close-triggered state
          // change clears editorTarget — pin it explicitly so the agent always
          // sees which file the user asked about.
          const pinned = {
            active_file_path: editorTarget?.name || editorTarget?.memberPath || null,
            active_file_id: editorTarget?.artifactId || null,
            ...(editorTarget?.focusLine ? { active_file_line: editorTarget.focusLine } : {}),
          };
          void sendRtlMessage(text, { context: pinned });
          setHasInteracted(true);
        }}
        isDarkTheme={isDarkTheme}
      />

      <IdeWorkspace
        open={ideOpen}
        onClose={dismissSurface}
        projectId={activeProject?.id}
        projectName={activeProject?.name}
        authToken={authToken}
        files={ideFiles}
        activeFileId={ideActiveFileId}
        onSelectFile={setIdeActiveFileId}
        onChangeFile={handleIdeChange}
        onSaveFile={handleIdeSave}
        output={(activeRunStatus?.events || []).slice(-200).map((e) => ({
          ts: e.created_at || e.timestamp,
          level: String(e.level || "info").toLowerCase(),
          message: String(e.message || ""),
        }))}
        events={(activeRunStatus?.events || []).map((e) => ({
          ts: e.created_at || e.timestamp,
          level: String(e.level || "info").toLowerCase(),
          phase: e.phase,
          agent: e.agent || e.agent_name,
          message: String(e.message || ""),
        }))}
        runStatus={activeRunStatus?.status}
        runId={activeRunId}
        agents={projectAgents}
        runs={projectRuns}
        recentDiff={recentDiff}
        isDarkTheme={isDarkTheme}
        threadTitle={mainThreadTitle}
        linkedMainThreadTitle={mainThreadTitle}
        assistantMessages={ideAssistantMessages}
        assistantLoading={ideCompanion.ideLoading}
        pendingAttachments={idePendingAttachmentChips}
        tokenContext={ideComposerTokenContext}
        onRemovePendingAttachment={(key) => {
          setIdeAttachments((prev) => prev.filter((r) => attachmentRowKey(r) !== key));
        }}
        onAttachFromExplorer={addIdeAttachment}
        onOpenWorkspaceAttach={() => setIdeAttachModalOpen(true)}
        onAttachUpload={() => triggerAttach("rtl")}
        onRenameFile={handleIdeRenameFile}
        onDeleteFile={handleIdeDeleteFile}
        onUploadFiles={handleIdeUploadFiles}
        explorerBusy={ideExplorerBusy}
        explorerError={ideExplorerError}
        busy={ideStreaming || ideCompanion.ideLoading}
        onCancelStream={cancelIdeStream}
        onAskAssistant={handleIdeAskAssistant}
        onOpenPalette={() => setPaletteOpen(true)}
        onOpenRun={() => { openProjectDashboard(); }}
        onOpenDiffInThread={openRecentDiffInThread}
        inlineCompletion={ideInlineCompletion}
        lintOptions={ideLintOptions}
        completionDebug={completionDebug}
      />

      <MentalModelOverlay
        open={mmOpen}
        onClose={dismissSurface}
        projectId={activeProject?.id}
        authToken={authToken}
        projectName={activeProject?.name}
        onBuilt={handleMentalModelBuilt}
        cadenceConnected={cadenceStatus?.tone === "ok"}
        hasUvmArtifacts={hasUvmArtifacts}
        graphChatMessages={graphChatMessages}
        graphChatDisabled={rtlStreaming}
        onGraphChatSend={handleMentalModelGraphChat}
        onCadenceAction={handleMentalModelCadenceAction}
        onOpenGraphChatInMainThread={() => {
          dismissSurface();
          graphChatMessages.slice(-3).forEach((m) => {
            if (m.role === "user") {
              pushItem(makeItem(THREAD_KINDS.USER, { text: m.text }));
            }
          });
        }}
      />

      <CodebaseGraphOverlay
        open={cgOpen}
        onClose={dismissSurface}
        projectId={activeProject?.id}
        authToken={authToken}
        projectName={activeProject?.name}
        onStatusChange={setCodebaseGraphStatus}
      />

      <DocumentationOverlay
        open={docsOpen}
        onClose={dismissSurface}
        authToken={authToken}
      />

      <FoundersOverlay
        open={foundersOpen}
        onClose={dismissSurface}
        isDarkTheme={isDarkTheme}
      />

      <ClarifierOverlay
        open={clarifier.isOpen}
        intro={clarifier.intro}
        stepIndex={clarifier.stepIndex}
        totalSteps={clarifier.totalSteps}
        step={clarifier.currentStep}
        answer={clarifier.currentAnswer}
        canAdvance={clarifier.canAdvance}
        isLastStep={clarifier.isLastStep}
        onPickChoice={clarifier.pickChoice}
        onCustomText={clarifier.setCustomText}
        onBack={clarifier.goBack}
        onNext={clarifier.goNext}
        onComplete={handleClarifierComplete}
        onDismiss={handleClarifierDismiss}
      />
    </div>

    <ProjectDrawer
      open={drawerOpen}
      onClose={() => setDrawerOpen(false)}
      runs={projectRuns}
      artifacts={recentArtifacts}
      patches={pendingPatches}
      threads={chatThreads.threads}
      activeThreadId={chatThreads.activeThreadId}
      onSwitchThread={(tid) => {
        setDrawerOpen(false);
        void chatThreads.switchThread(tid);
      }}
      onNewThread={() => {
        setDrawerOpen(false);
        void chatThreads.createThread({ title: "Project Chat", switchTo: true })
          .then(() => setHasInteracted(false));
      }}
      onDeleteThread={(tid) => {
        void chatThreads.deleteThread(tid);
        setRunForThread((prev) => {
          if (!prev[tid]) return prev;
          const { [tid]: _drop, ...rest } = prev;
          if (runForThreadStorageKey) {
            try { window.localStorage.setItem(runForThreadStorageKey, JSON.stringify(rest)); } catch { /* quota / private mode */ }
          }
          return rest;
        });
      }}
      onOpenPatch={(p) => { setDrawerOpen(false); scrollToPatch(p.id); }}
      onOpenRun={(run) => {
        setDrawerOpen(false);
        const id = run?.id || run?.run_id;
        if (id) onSelectActiveRun?.(id);
        openProjectDashboard();
      }}
      onOpenFile={(a) => {
        setDrawerOpen(false);
        openFile({
          artifactId: a.artifactId || a.id,
          memberPath: a.memberPath || undefined,
          name: a.memberPath || a.filename || a.metadata?.relative_path,
        });
      }}
      onOpenDashboard={() => { setDrawerOpen(false); openProjectDashboard(); }}
      onOpenIde={() => { setDrawerOpen(false); openPrimary("ide"); }}
      onOpenDocs={() => { setDrawerOpen(false); openPrimary("docs"); }}
      cadenceStatus={cadenceStatus.loading ? null : cadenceStatus}
      onConfigureCadence={() => { setDrawerOpen(false); setCadenceConfigOpen(true); }}
      onRestartTour={() => {
        setDrawerOpen(false);
        onboarding.restartTour();
      }}
    />
    </>
  );
}
