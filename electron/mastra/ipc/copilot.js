const { randomUUID } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const {
  getRuntime,
  streamCopilotResponse,
  approveCopilotToolCall,
  declineCopilotToolCall,
} = require("../runtime");
const { logDebug } = require("../debugLogger");

let hasWarnedDroppedCopilotEvents = false;

const DEFAULT_BACKEND_URL = "http://127.0.0.1:7348";
const WORKSPACE_COMMAND_TIMEOUT_MS = 30_000;
const WORKSPACE_MAX_OUTPUT_CHARS = 24_000;

const workspaceBashCache = new Map();

function ensureWorkspaceRootExists(workspaceRoot) {
  const resolvedRoot = resolveWorkspaceRoot(workspaceRoot);

  if (!fs.existsSync(resolvedRoot)) {
    fs.mkdirSync(resolvedRoot, { recursive: true });
  }

  return resolvedRoot;
}

function resolveProjectIdFromPayload(payload = {}) {
  const direct = String(payload?.projectId || "").trim();
  if (direct) return direct;

  const context = payload?.context || {};
  const fromContext =
    String(context?.projectId || "").trim()
    || String(context?.project_id || "").trim()
    || String(context?.activeProjectId || "").trim();

  return fromContext || undefined;
}

function resolveWorkspaceRoot(hint) {
  const raw = hint || process.env.CHIPVERIFY_WORKSPACE_ROOT || process.cwd();
  return path.resolve(raw);
}

function isSimpleGreetingPrompt(prompt) {
  const value = String(prompt || "").trim().toLowerCase();
  return /^(hi|hello|hey|yo|hola|hiya|good\s+(morning|afternoon|evening))[!. ]*$/.test(value);
}

function configuredBackendProvider() {
  return String(process.env.CHIPVERIFY_LLM_PROVIDER || process.env.MODEL_PROVIDER || "")
    .trim()
    .toLowerCase()
    .replace(/-/g, "_");
}

function shouldRouteCopilotThroughBackend() {
  if (String(process.env.CHIPVERIFY_MASTRA_MODEL || process.env.CHIPVERIFY_MODEL || "").trim()) {
    return false;
  }
  return configuredBackendProvider() === "bedrock";
}

function sanitizeCommandOutput(text, maxChars = WORKSPACE_MAX_OUTPUT_CHARS) {
  if (!text) {
    return "";
  }

  const value = String(text);
  if (value.length <= maxChars) {
    return value;
  }

  const half = Math.floor(maxChars / 2);
  return `${value.slice(0, half)}\n... [truncated ${value.length - maxChars} chars] ...\n${value.slice(-half)}`;
}

function getOrCreateWorkspaceBash(workspaceRoot) {
  const resolvedRoot = ensureWorkspaceRootExists(workspaceRoot);
  if (workspaceBashCache.has(resolvedRoot)) {
    return workspaceBashCache.get(resolvedRoot);
  }

  const { Bash, ReadWriteFs } = require("just-bash");
  const fs = new ReadWriteFs({ root: resolvedRoot });
  const bash = new Bash({
    fs,
    cwd: "/",
    executionLimits: {
      maxCommandCount: 2000,
      maxLoopIterations: 5000,
    },
  });

  const entry = { bash, workspaceRoot: resolvedRoot };
  workspaceBashCache.set(resolvedRoot, entry);
  return entry;
}

function normalizeBackendBaseUrl(rawUrl) {
  if (typeof rawUrl !== "string") {
    return "";
  }

  const trimmed = rawUrl.trim();
  if (!trimmed) {
    return "";
  }

  const withoutApiPath = trimmed.replace(/\/+$/, "").replace(/\/api\/v1$/i, "");

  try {
    const parsed = new URL(withoutApiPath);
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString().replace(/\/+$/, "");
  } catch {
    return withoutApiPath;
  }
}

function isMastraEsmFailure(error) {
  const message = String(error?.message || error || "");
  const code = String(error?.code || "");
  return (
    code === "ERR_REQUIRE_ESM" ||
    message.includes("ERR_REQUIRE_ESM") ||
    message.toLowerCase().includes("tokenx")
  );
}

function isMastraMissingProviderConfig(error) {
  const message = String(error?.message || error || "").toLowerCase();
  return (
    message.includes("could not find api key process.env") ||
    message.includes("missing api key") ||
    message.includes("for model id")
  );
}

function isMastraNetworkFailure(error) {
  const message = String(error?.message || error || "").toLowerCase();
  const causeCode = String(error?.cause?.code || error?.code || "").toUpperCase();
  return message === "fetch failed" || ["ECONNREFUSED", "ECONNRESET", "ENOTFOUND", "ETIMEDOUT"].includes(causeCode);
}

function isMastraRecoverableFailure(error) {
  return isMastraEsmFailure(error) || isMastraMissingProviderConfig(error) || isMastraNetworkFailure(error);
}

function resolveRuntimeModelMetadata() {
  const configuredModel =
    String(process.env.CHIPVERIFY_MASTRA_MODEL || "").trim()
    || String(process.env.CHIPVERIFY_MODEL || "").trim();

  if (configuredModel.includes("/")) {
    const [provider] = configuredModel.split("/");
    return {
      provider: String(provider || "unknown").toLowerCase(),
      model: configuredModel,
    };
  }

  const backendProvider = configuredBackendProvider();
  if (backendProvider === "bedrock") {
    return {
      provider: "bedrock",
      model: String(process.env.BEDROCK_MODEL || process.env.MODEL_NAME || "deepseek.v3.2").trim(),
    };
  }

  if (process.env.GOOGLE_GENERATIVE_AI_API_KEY || process.env.GOOGLE_API_KEY) {
    return { provider: "google", model: "google/gemini-3.1-pro-preview" };
  }

  if (process.env.OPENAI_API_KEY) {
    return { provider: "openai", model: "openai/gpt-4o-mini" };
  }

  return {
    provider: "unknown",
    model: configuredModel || null,
  };
}

function summarizeFallbackContext(context = {}) {
  const source = context || {};
  return {
    keys: Object.keys(source),
    projectId: source.projectId || source.project_id || source.activeProjectId || null,
    threadId: source.threadId || source.thread_id || null,
    workspaceRoot: source.workspaceRoot || null,
  };
}

async function askBackendFallback({ prompt, projectId, threadId, context, telemetry = {} }) {
  const backendBaseUrl =
    normalizeBackendBaseUrl(process.env.CHIPVERIFY_BACKEND_URL || "") || DEFAULT_BACKEND_URL;

  let resolvedThreadId = threadId;
  let threadSource = resolvedThreadId ? "payload" : "none";

  // Prefer project-scoped thread when available.
  if (projectId) {
    try {
      logDebug(
        "ipc.fallback.thread.create.start",
        {
          backendBaseUrl,
          projectId,
        },
        {
          component: "ipc.copilot",
          requestId: telemetry.requestId,
          workspaceRoot: context?.workspaceRoot,
        },
      );

      const threadRes = await fetch(`${backendBaseUrl}/api/v1/projects/${projectId}/chat/threads`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "Agent Chat" }),
      });

      if (threadRes.ok) {
        const threadJson = await threadRes.json().catch(() => null);
        if (threadJson?.id) {
          resolvedThreadId = threadJson.id;
          threadSource = "project_create";
        }
      } else {
        const threadErrBody = await threadRes.json().catch(() => null);
        logDebug(
          "ipc.fallback.thread.create.error",
          {
            status: threadRes.status,
            projectId,
            error: threadErrBody?.detail || threadErrBody?.message || null,
          },
          {
            component: "ipc.copilot",
            requestId: telemetry.requestId,
            workspaceRoot: context?.workspaceRoot,
          },
        );
      }

      logDebug(
        "ipc.fallback.thread.create.completed",
        {
          status: threadRes.status,
          resolvedThreadId,
          threadSource,
        },
        {
          component: "ipc.copilot",
          requestId: telemetry.requestId,
          workspaceRoot: context?.workspaceRoot,
        },
      );
    } catch (threadCreateError) {
      logDebug(
        "ipc.fallback.thread.create.exception",
        {
          message: threadCreateError?.message || String(threadCreateError),
          projectId,
        },
        {
          component: "ipc.copilot",
          requestId: telemetry.requestId,
          workspaceRoot: context?.workspaceRoot,
        },
      );
      // Ignore thread creation failures and fall back to provided/local thread id.
    }
  }

  if (!resolvedThreadId) {
    logDebug(
      "ipc.fallback.thread.unavailable",
      {
        reason: "missing_thread_id_and_project_id",
        projectId,
        contextSummary: summarizeFallbackContext(context),
      },
      {
        component: "ipc.copilot",
        requestId: telemetry.requestId,
        workspaceRoot: context?.workspaceRoot,
      },
    );

    throw new Error(
      "Backend fallback cannot create chat thread: missing projectId and no existing threadId.",
    );
  }

  const askUrl = `${backendBaseUrl}/api/v1/chat/threads/${resolvedThreadId}/ask`;

  logDebug(
    "ipc.fallback.ask.start",
    {
      backendBaseUrl,
      askUrl,
      resolvedThreadId,
      threadSource,
      projectId,
      prompt,
      contextSummary: summarizeFallbackContext(context),
    },
    {
      component: "ipc.copilot",
      requestId: telemetry.requestId,
      workspaceRoot: context?.workspaceRoot,
    },
  );

  const askRes = await fetch(askUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt,
      context: {
        ...(context || {}),
        ...(projectId ? { project_id: projectId, projectId } : {}),
        thread_id: resolvedThreadId,
      },
    }),
  });

  if (!askRes.ok) {
    const errJson = await askRes.json().catch(() => null);

    console.warn("[Mastra IPC] backend fallback ask failed", {
      requestId: telemetry.requestId,
      status: askRes.status,
      askUrl,
      threadId: resolvedThreadId,
      threadSource,
      projectId,
      error: errJson?.detail || errJson?.message || null,
    });

    logDebug(
      "ipc.fallback.ask.error",
      {
        status: askRes.status,
        askUrl,
        threadId: resolvedThreadId,
        threadSource,
        projectId,
        contextSummary: summarizeFallbackContext(context),
        error: errJson?.detail || errJson?.message || null,
      },
      {
        component: "ipc.copilot",
        requestId: telemetry.requestId,
        workspaceRoot: context?.workspaceRoot,
      },
    );
    throw new Error(errJson?.detail || errJson?.message || `Backend fallback failed with ${askRes.status}`);
  }

  const askJson = await askRes.json().catch(() => null);
  const text = String(askJson?.assistant_message?.content || "").trim();

  logDebug(
    "ipc.fallback.ask.completed",
    {
      status: askRes.status,
      responseLength: text.length,
      askUrl,
      threadId: resolvedThreadId,
      threadSource,
    },
    {
      component: "ipc.copilot",
      requestId: telemetry.requestId,
      workspaceRoot: context?.workspaceRoot,
    },
  );

  return { text, threadId: resolvedThreadId };
}

function registerMastraCopilotIpc({ ipcMain, getMainWindow }) {
  const activeRuns = new Map();
  console.info("[Mastra IPC] Registering Copilot IPC handlers");

  // Bedrock is served by the Python backend's OpenAI-compatible client. Avoid
  // constructing Mastra's default OpenAI model, which would make a doomed
  // network request before falling back to the backend.
  if (!shouldRouteCopilotThroughBackend()) {
    void getRuntime().catch((err) => {
      console.warn("[Mastra IPC] Runtime pre-warm failed:", err?.message);
    });
  }

  function emitToRenderer(payload) {
    const win = getMainWindow?.();
    if (!win || win.isDestroyed()) {
      if (!hasWarnedDroppedCopilotEvents) {
        hasWarnedDroppedCopilotEvents = true;
        console.warn("[Mastra IPC] Dropping Copilot events — no active renderer window");
      }
      return;
    }
    win.webContents.send("chipverify:copilot:event", payload);
  }

  // ── chipverify:copilot:start ─────────────────────────────────────────────
  ipcMain.handle("chipverify:copilot:start", async (_event, payload = {}) => {
    const requestId = randomUUID();
    const prompt = String(payload.prompt || "").trim();
    const resolvedProjectId = resolveProjectIdFromPayload(payload);

    if (!prompt) {
      return { ok: false, error: "Prompt is required." };
    }

    const runState = {
      requestId,
      cancelled: false,
      suspended: false,
      mastraRunId: null,
      finalText: "",
      projectId: resolvedProjectId,
      threadId: payload.threadId,
      userId: payload.userId,
      context: payload.context || {},
      backendFirst: shouldRouteCopilotThroughBackend(),
      metrics: {
        textDeltaChars: 0,
        textDeltaEvents: 0,
        toolCalls: 0,
        toolResults: 0,
      },
    };
    activeRuns.set(requestId, runState);

    logDebug(
      "ipc.run.start",
      {
        requestId,
        prompt,
        projectId: resolvedProjectId,
        threadId: payload.threadId,
        context: payload.context || {},
        modelMeta: resolveRuntimeModelMetadata(),
      },
      {
        component: "ipc.copilot",
        requestId,
        workspaceRoot: payload?.context?.workspaceRoot,
      },
    );

    // Run asynchronously so IPC can return the requestId immediately
    queueMicrotask(async () => {
      try {
        if (runState.backendFirst) {
          runState.mastraRunId = requestId;
          emitToRenderer({ requestId, type: "run_started", runId: requestId, traceId: null });

          let text;
          if (isSimpleGreetingPrompt(prompt)) {
            const workspaceRoot = resolveWorkspaceRoot(runState.context?.workspaceRoot);
            text = `Hi! I'm ChipVerify Copilot. I have direct access to your workspace at ${workspaceRoot}. I can list, read, edit, and search files, plus help with RTL verification workflows.`;
          } else {
            const fallback = await askBackendFallback({
              prompt,
              projectId: resolvedProjectId,
              threadId: payload.threadId,
              context: payload.context || {},
              telemetry: { requestId },
            });
            text = String(fallback?.text || "").trim();
          }

          if (text) {
            emitToRenderer({ requestId, type: "text_delta", runId: requestId, text });
            runState.metrics.textDeltaEvents += 1;
            runState.metrics.textDeltaChars += text.length;
            runState.finalText += text;
          }
          emitToRenderer({ requestId, type: "run_completed", runId: requestId });
          return;
        }

        const result = await streamCopilotResponse({
          prompt,
          projectId: resolvedProjectId,
          threadId: payload.threadId,
          userId: payload.userId,
          context: payload.context || {},  // ← workspace context forwarded
          onEvent: (event) => {
            if (runState.cancelled) return;

            if (event?.type === "run_started" && event?.runId) {
              runState.mastraRunId = event.runId;
            }

            if (event?.type === "text_delta") {
              const text = String(event?.text || "");
              runState.metrics.textDeltaEvents += 1;
              runState.metrics.textDeltaChars += text.length;
              runState.finalText += text;

              logDebug(
                "ipc.run.event.text_delta",
                {
                  length: text.length,
                  preview: text.slice(0, 400),
                },
                {
                  component: "ipc.copilot",
                  requestId,
                  runId: runState.mastraRunId,
                  workspaceRoot: runState.context?.workspaceRoot,
                },
              );
            }

            if (event?.type === "tool_call_update") {
              const status = String(event?.toolCall?.status || "").toLowerCase();
              if (status === "input-available") {
                runState.metrics.toolCalls += 1;
              }
              if (status === "output-available" || status === "error") {
                runState.metrics.toolResults += 1;
              }

              if (status === "awaiting_confirmation") {
                runState.suspended = true;
              }

              logDebug(
                "ipc.run.event.tool",
                {
                  status,
                  tool: event?.toolCall?.tool || event?.toolCall?.name || "tool",
                  args: event?.toolCall?.args || event?.toolCall?.arguments || {},
                  result: event?.toolCall?.result ?? event?.toolCall?.output ?? null,
                },
                {
                  component: "ipc.copilot",
                  requestId,
                  runId: runState.mastraRunId,
                  workspaceRoot: runState.context?.workspaceRoot,
                },
              );
            }

            emitToRenderer({ requestId, ...event });
          },
        });

        runState.suspended = Boolean(result?.suspended || runState.suspended);

        if (!runState.suspended && !runState.cancelled && runState.metrics.textDeltaEvents === 0) {
          if (isSimpleGreetingPrompt(prompt)) {
            const workspaceRoot = resolveWorkspaceRoot(runState.context?.workspaceRoot);
            const greetingText = `Hi! I'm ChipVerify Copilot. I have direct access to your workspace at ${workspaceRoot}. I can list, read, edit, and search files, plus help with RTL verification workflows.`;

            logDebug(
              "ipc.run.empty_response.greeting_short_circuit",
              {
                metrics: runState.metrics,
                action: "emit_local_greeting",
              },
              {
                component: "ipc.copilot",
                requestId,
                runId: runState.mastraRunId,
                workspaceRoot: runState.context?.workspaceRoot,
              },
            );

            emitToRenderer({ requestId, type: "text_delta", runId: runState.mastraRunId || requestId, text: greetingText });
            runState.metrics.textDeltaEvents += 1;
            runState.metrics.textDeltaChars += greetingText.length;
            runState.finalText += greetingText;
            return;
          }

          logDebug(
            "ipc.run.empty_response.detected",
            {
              metrics: runState.metrics,
              action: "attempt_backend_fallback",
            },
            {
              component: "ipc.copilot",
              requestId,
              runId: runState.mastraRunId,
              workspaceRoot: runState.context?.workspaceRoot,
            },
          );

          try {
            const fallback = await askBackendFallback({
              prompt,
              projectId: resolvedProjectId,
              threadId: payload.threadId,
              context: payload.context || {},
              telemetry: { requestId },
            });

            if (fallback?.text) {
              emitToRenderer({ requestId, type: "text_delta", runId: runState.mastraRunId || requestId, text: fallback.text });
              runState.metrics.textDeltaEvents += 1;
              runState.metrics.textDeltaChars += String(fallback.text).length;
              runState.finalText += String(fallback.text);
            } else {
              const emptyFallbackText = "I completed tool steps but did not produce a final response. Please retry, and logs are now captured for diagnosis.";
              emitToRenderer({
                requestId,
                type: "text_delta",
                runId: runState.mastraRunId || requestId,
                text: emptyFallbackText,
              });
              runState.metrics.textDeltaEvents += 1;
              runState.metrics.textDeltaChars += emptyFallbackText.length;
              runState.finalText += emptyFallbackText;
            }
          } catch (emptyFallbackError) {
            logDebug(
              "ipc.run.empty_response.fallback_failed",
              {
                message: emptyFallbackError?.message || String(emptyFallbackError),
              },
              {
                component: "ipc.copilot",
                requestId,
                runId: runState.mastraRunId,
                workspaceRoot: runState.context?.workspaceRoot,
              },
            );

            const fallbackErrorMessage = String(emptyFallbackError?.message || "");
            const missingProviderKey = /api key|model id|openai_api_key/i.test(fallbackErrorMessage);

            const finalFallbackText = missingProviderKey
              ? "I could not respond because the runtime model is missing required provider API keys, and backend fallback was unavailable. Please configure a provider key (for example GOOGLE_GENERATIVE_AI_API_KEY or OPENAI_API_KEY) and try again."
              : "I could not produce a final response because both runtime output and backend fallback failed. Please retry; detailed diagnostics were written to backend/logs/mastra-copilot-debug.ndjson.";

            emitToRenderer({
              requestId,
              type: "text_delta",
              runId: runState.mastraRunId || requestId,
              text: finalFallbackText,
            });
            runState.metrics.textDeltaEvents += 1;
            runState.metrics.textDeltaChars += finalFallbackText.length;
          }
        }
      } catch (error) {
        if (!runState.backendFirst && isMastraRecoverableFailure(error)) {
          console.warn("[Mastra IPC] Mastra runtime degraded. Falling back to backend /ask.", {
            requestId,
            message: error?.message,
          });

          logDebug(
            "ipc.run.error.recoverable",
            {
              message: error?.message || String(error),
            },
            {
              component: "ipc.copilot",
              requestId,
              runId: runState.mastraRunId,
              workspaceRoot: runState.context?.workspaceRoot,
            },
          );

          try {
            emitToRenderer({ requestId, type: "run_started", runId: requestId, traceId: null });
            const fallback = await askBackendFallback({
              prompt,
              projectId: resolvedProjectId,
              threadId: payload.threadId,
              context: payload.context || {},
              telemetry: { requestId },
            });

            if (fallback?.text) {
              emitToRenderer({ requestId, type: "text_delta", runId: requestId, text: fallback.text });
              runState.finalText += String(fallback.text);
            }

            emitToRenderer({ requestId, type: "run_completed", runId: requestId });
          } catch (fallbackError) {
            console.error("[Mastra IPC] backend fallback failed", { requestId, fallbackError });
            emitToRenderer({ requestId, type: "run_error", message: fallbackError?.message || String(fallbackError) });
          }
        } else {
          console.error("[Mastra IPC] start handler stream failure", { requestId, error });

          logDebug(
            "ipc.run.error.fatal",
            {
              message: error?.message || String(error),
              stack: error?.stack || null,
            },
            {
              component: "ipc.copilot",
              requestId,
              runId: runState.mastraRunId,
              workspaceRoot: runState.context?.workspaceRoot,
            },
          );

          emitToRenderer({ requestId, type: "run_error", message: error?.message || String(error) });
        }
      } finally {
        logDebug(
          "ipc.run.finalized",
          {
            suspended: runState.suspended,
            cancelled: runState.cancelled,
            metrics: runState.metrics,
          },
          {
            component: "ipc.copilot",
            requestId,
            runId: runState.mastraRunId,
            workspaceRoot: runState.context?.workspaceRoot,
          },
        );

        if (runState.suspended && !runState.cancelled) {
          emitToRenderer({ requestId, type: "run_suspended", runId: runState.mastraRunId });
        } else {
          emitToRenderer({ requestId, type: "run_finalized", finalText: runState.finalText || "" });
          activeRuns.delete(requestId);
        }
      }
    });

    return { ok: true, requestId };
  });

  // ── chipverify:copilot:health ────────────────────────────────────────────
  ipcMain.handle("chipverify:copilot:health", async () => {
    const modelMeta = resolveRuntimeModelMetadata();

    if (shouldRouteCopilotThroughBackend()) {
      return {
        ok: true,
        status: "ready",
        message: "Copilot is using the configured backend provider.",
        details: {
          hasAgent: true,
          hasWorkspace: true,
          fallbackActive: true,
          runtimePath: "backend",
          provider: modelMeta.provider,
          model: modelMeta.model,
        },
        checkedAt: Date.now(),
      };
    }

    try {
      const runtime = await getRuntime();
      const hasAgent = Boolean(runtime?.agent);
      const hasWorkspace = Boolean(runtime?.workspace || runtime?.workspaceRoot);

      if (hasAgent && hasWorkspace) {
        return {
          ok: true,
          status: "ready",
          message: "Electron Copilot runtime is healthy.",
          details: {
            hasAgent,
            hasWorkspace,
            fallbackActive: false,
            runtimePath: "mastra",
            provider: modelMeta.provider,
            model: modelMeta.model,
          },
          checkedAt: Date.now(),
        };
      }

      return {
        ok: false,
        status: "degraded",
        message: "Electron Copilot runtime initialized incompletely.",
        details: { hasAgent, hasWorkspace },
        checkedAt: Date.now(),
      };
    } catch (error) {
      if (isMastraRecoverableFailure(error)) {
        return {
          ok: true,
          status: "degraded",
          message: "Mastra runtime unavailable; backend fallback is active.",
          details: {
            hasAgent: false,
            hasWorkspace: false,
            fallbackActive: true,
            runtimePath: "fallback",
            reason: isMastraEsmFailure(error) ? "esm_incompatibility" : "provider_config",
            provider: modelMeta.provider,
            model: modelMeta.model,
          },
          checkedAt: Date.now(),
        };
      }

      return {
        ok: false,
        status: "missing",
        message: error?.message || "Electron Copilot runtime is unavailable.",
        details: { hasAgent: false, hasWorkspace: false },
        checkedAt: Date.now(),
      };
    }
  });

  // ── chipverify:workspace:bash ────────────────────────────────────────────
  ipcMain.handle("chipverify:workspace:bash", async (_event, payload = {}) => {
    const command = String(payload.command || "").trim();
    const workspaceRoot = ensureWorkspaceRootExists(payload.workspaceRoot);

    if (!command) {
      return { ok: false, error: "Command is required." };
    }

    try {
      const { bash } = getOrCreateWorkspaceBash(workspaceRoot);
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), WORKSPACE_COMMAND_TIMEOUT_MS);

      let result;
      try {
        result = await bash.exec(command, { signal: controller.signal });
      } finally {
        clearTimeout(timeout);
      }

      const stdout = sanitizeCommandOutput(result?.stdout || "");
      const stderr = sanitizeCommandOutput(result?.stderr || "");
      const exitCode = Number(result?.exitCode ?? 1);

      return {
        ok: exitCode === 0,
        exitCode,
        stdout,
        stderr,
        workspaceRoot,
      };
    } catch (error) {
      const errorMessage = String(error?.message || error || "");

      // Recovery path: if just-bash reports a missing root, recreate it, invalidate cache, and retry once.
      if (/readwritefs root does not exist/i.test(errorMessage)) {
        try {
          workspaceBashCache.delete(workspaceRoot);
          ensureWorkspaceRootExists(workspaceRoot);

          const { bash } = getOrCreateWorkspaceBash(workspaceRoot);
          const retryController = new AbortController();
          const retryTimeout = setTimeout(() => retryController.abort(), WORKSPACE_COMMAND_TIMEOUT_MS);

          let retryResult;
          try {
            retryResult = await bash.exec(command, { signal: retryController.signal });
          } finally {
            clearTimeout(retryTimeout);
          }

          const retryStdout = sanitizeCommandOutput(retryResult?.stdout || "");
          const retryStderr = sanitizeCommandOutput(retryResult?.stderr || "");
          const retryExitCode = Number(retryResult?.exitCode ?? 1);

          return {
            ok: retryExitCode === 0,
            exitCode: retryExitCode,
            stdout: retryStdout,
            stderr: retryStderr,
            workspaceRoot,
          };
        } catch (retryError) {
          if (retryError?.name === "AbortError") {
            return {
              ok: false,
              exitCode: 124,
              error: `Command timed out after ${WORKSPACE_COMMAND_TIMEOUT_MS / 1000}s`,
              workspaceRoot,
            };
          }

          return {
            ok: false,
            exitCode: 1,
            error: retryError?.message || String(retryError),
            workspaceRoot,
          };
        }
      }

      if (error?.name === "AbortError") {
        return {
          ok: false,
          exitCode: 124,
          error: `Command timed out after ${WORKSPACE_COMMAND_TIMEOUT_MS / 1000}s`,
          workspaceRoot,
        };
      }

      return {
        ok: false,
        exitCode: 1,
        error: error?.message || String(error),
        workspaceRoot,
      };
    }
  });

  // ── chipverify:copilot:stop ──────────────────────────────────────────────
  ipcMain.handle("chipverify:copilot:stop", async (_event, payload = {}) => {
    const requestId = String(payload.requestId || "");
    const run = activeRuns.get(requestId);

    if (!run) {
      return { ok: false, error: "Run not found." };
    }

    run.cancelled = true;
    activeRuns.delete(requestId);
    emitToRenderer({ requestId, type: "run_cancelled" });

    return { ok: true };
  });

  // ── chipverify:copilot:approve-tool ─────────────────────────────────────
  ipcMain.handle("chipverify:copilot:approve-tool", async (_event, payload = {}) => {
    const requestId = String(payload.requestId || "");
    const toolCallId = payload.toolCallId ? String(payload.toolCallId) : undefined;
    const run = activeRuns.get(requestId);

    if (!run || !run.mastraRunId) {
      return { ok: false, error: "Run not found or not awaiting approval." };
    }

    try {
      const result = await approveCopilotToolCall({
        runId: run.mastraRunId,
        toolCallId,
        projectId: run.projectId,
        threadId: run.threadId,
        userId: run.userId,
        context: run.context || {},
        onEvent: (event) => {
          if (event?.type === "text_delta") {
            run.finalText = `${run.finalText || ""}${String(event?.text || "")}`;
          }
          if (event?.type === "tool_call_update") {
            const status = String(event?.toolCall?.status || "").toLowerCase();
            run.suspended = status === "awaiting_confirmation";
          }
          emitToRenderer({ requestId, ...event });
        },
      });

      run.mastraRunId = result?.runId || run.mastraRunId;
      run.suspended = Boolean(result?.suspended);

      if (!run.suspended) {
        emitToRenderer({ requestId, type: "run_finalized", finalText: run.finalText || "" });
        activeRuns.delete(requestId);
      } else {
        emitToRenderer({ requestId, type: "run_suspended", runId: run.mastraRunId });
      }

      return { ok: true };
    } catch (error) {
      console.error("[Mastra IPC] approve-tool handler failed", { requestId, error });
      emitToRenderer({ requestId, type: "run_error", message: error?.message || String(error) });
      return { ok: false, error: error?.message || "Failed to approve tool call." };
    }
  });

  // ── chipverify:copilot:decline-tool ─────────────────────────────────────
  ipcMain.handle("chipverify:copilot:decline-tool", async (_event, payload = {}) => {
    const requestId = String(payload.requestId || "");
    const toolCallId = payload.toolCallId ? String(payload.toolCallId) : undefined;
    const run = activeRuns.get(requestId);

    if (!run || !run.mastraRunId) {
      return { ok: false, error: "Run not found or not awaiting approval." };
    }

    try {
      const result = await declineCopilotToolCall({
        runId: run.mastraRunId,
        toolCallId,
        projectId: run.projectId,
        threadId: run.threadId,
        userId: run.userId,
        context: run.context || {},
        onEvent: (event) => {
          if (event?.type === "text_delta") {
            run.finalText = `${run.finalText || ""}${String(event?.text || "")}`;
          }
          if (event?.type === "tool_call_update") {
            const status = String(event?.toolCall?.status || "").toLowerCase();
            run.suspended = status === "awaiting_confirmation";
          }
          emitToRenderer({ requestId, ...event });
        },
      });

      run.mastraRunId = result?.runId || run.mastraRunId;
      run.suspended = Boolean(result?.suspended);

      if (!run.suspended) {
        emitToRenderer({ requestId, type: "run_finalized", finalText: run.finalText || "" });
        activeRuns.delete(requestId);
      } else {
        emitToRenderer({ requestId, type: "run_suspended", runId: run.mastraRunId });
      }

      return { ok: true };
    } catch (error) {
      console.error("[Mastra IPC] decline-tool handler failed", { requestId, error });
      emitToRenderer({ requestId, type: "run_error", message: error?.message || String(error) });
      return { ok: false, error: error?.message || "Failed to decline tool call." };
    }
  });
}

module.exports = {
  registerMastraCopilotIpc,
  _test: {
    isMastraRecoverableFailure,
    resolveRuntimeModelMetadata,
    shouldRouteCopilotThroughBackend,
  },
};
