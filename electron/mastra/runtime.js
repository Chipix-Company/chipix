/**
 * runtime.js
 * ==========
 * Initializes the Mastra Copilot runtime:
 *  - Mastra workspace (for memory + agent lifecycle)
 *  - Memory (LibSQL-backed, persists across restarts)
 *  - bash tool (just-bash + ReadWriteFs on real workspace directory)
 *  - workspace filesystem tools (structured list/read/write/grep)
 *  - backend bridge tools (Python backend API bridge)
 *  - workspaceCopilotAgent (wires all tools together)
 *
 * Singleton pattern: one runtime per workspaceRoot.
 * If no workspaceRoot is specified, defaults to CHIPVERIFY_WORKSPACE_ROOT
 * env var or process.cwd().
 */

const { randomUUID } = require("node:crypto");
const path = require("node:path");
const fs = require("node:fs");
const { logDebug } = require("./debugLogger");
const { createProjectScopedMemory, buildMemoryScope } = require("./memory");
const { createWorkspaceCopilotAgent } = require("./agents/workspaceCopilotAgent");
const {
  createBackendBridgeTools,
  createWorkspaceBashTool,
  createWorkspaceFilesystemTools,
} = require("./tools");

// ── Runtime cache keyed by resolved workspace root ─────────────────────────
const runtimeCache = new Map();
let defaultRuntimePromise = null;

// ── Environment loading (shared with Electron + Node entry points) ───────
function parseEnvValue(rawValue) {
  const trimmed = String(rawValue || "").trim();
  if (!trimmed) {
    return "";
  }

  const first = trimmed[0];
  const last = trimmed[trimmed.length - 1];
  if ((first === '"' && last === '"') || (first === "'" && last === "'")) {
    return trimmed.slice(1, -1);
  }

  return trimmed;
}

function loadEnvFile(filePath) {
  try {
    if (!fs.existsSync(filePath)) {
      return false;
    }

    const content = fs.readFileSync(filePath, "utf8");
    const lines = content.split(/\r?\n/);

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) {
        continue;
      }

      const separatorIndex = trimmed.indexOf("=");
      if (separatorIndex <= 0) {
        continue;
      }

      const key = trimmed.slice(0, separatorIndex).trim();
      if (!key || process.env[key] !== undefined) {
        continue;
      }

      const rawValue = trimmed.slice(separatorIndex + 1);
      process.env[key] = parseEnvValue(rawValue);
    }

    return true;
  } catch (error) {
    console.warn("[Mastra Runtime] Failed to load env file", { filePath, message: error?.message });
    return false;
  }
}

function ensureRuntimeEnvLoaded(workspaceRoot) {
  const projectRoot = path.resolve(workspaceRoot || process.cwd());
  loadEnvFile(path.join(projectRoot, ".env"));
  loadEnvFile(path.join(projectRoot, "backend", ".env"));

  if (!process.env.GOOGLE_GENERATIVE_AI_API_KEY && process.env.GOOGLE_API_KEY) {
    process.env.GOOGLE_GENERATIVE_AI_API_KEY = process.env.GOOGLE_API_KEY;
  }
}

// ── Workspace root resolution ──────────────────────────────────────────────
function resolveWorkspaceRoot(hint) {
  const raw = hint
    || process.env.CHIPVERIFY_WORKSPACE_ROOT
    || process.cwd();
  return path.resolve(raw);
}

const DEFAULT_BACKEND_URL = "http://127.0.0.1:7348";
const AUTO_READ_MAX_FILES = 10;
const AUTO_READ_MAX_FILE_CHARS = 7000;
const AUTO_READ_MAX_TOTAL_CHARS = 32000;

const CODE_AGENT_PROMPT_RE =
  /\b(explain|describe|summari[sz]e|understand|design|architecture|connection|connect|hierarchy|module|rtl|verilog|systemverilog|spec|testbench|uvm|pipeline|workflow|why|how|what|where|bug|error|fail|failing|warning|report|mental\s+model|trace|relationship|dependency)\b/i;

const PROMPT_STOPWORDS = new Set([
  "the", "and", "for", "with", "this", "that", "from", "into", "about",
  "can", "you", "please", "tell", "explain", "design", "code", "file",
  "files", "project", "what", "where", "when", "why", "how", "does",
]);

function normalizeBackendBaseUrl(rawUrl) {
  const trimmed = String(rawUrl || "").trim();
  if (!trimmed) return DEFAULT_BACKEND_URL;
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

function isSimpleGreetingPrompt(prompt) {
  return /^(hi|hello|hey|yo|hola|hiya|good\s+(morning|afternoon|evening))[!. ]*$/i
    .test(String(prompt || "").trim());
}

function promptTokens(prompt) {
  return Array.from(
    new Set(
      String(prompt || "")
        .toLowerCase()
        .split(/[^a-z0-9_.$-]+/)
        .map((token) => token.trim())
        .filter((token) => token.length >= 3 && !PROMPT_STOPWORDS.has(token)),
    ),
  ).slice(0, 24);
}

function getArtifactDisplayName(file = {}) {
  return String(
    file?.relative_path
      || file?.metadata?.relative_path
      || file?.metadata?.source_relative_path
      || file?.filename
      || "",
  ).trim();
}

function getVerificationContextIds(context = {}) {
  const verificationContext = context.verification_context || context.verificationContext || {};
  return new Set(
    [
      verificationContext.selected_spec_id,
      verificationContext.selectedSpecId,
      verificationContext.selected_rtl_id,
      verificationContext.selectedRtlId,
      verificationContext.active_spec?.id,
      verificationContext.activeSpec?.id,
      verificationContext.active_rtl?.id,
      verificationContext.activeRtl?.id,
    ]
      .map((value) => String(value || "").trim())
      .filter(Boolean),
  );
}

function getAttachedArtifactIds(context = {}) {
  const attached = []
    .concat(Array.isArray(context.attached_files) ? context.attached_files : [])
    .concat(Array.isArray(context.attachedFiles) ? context.attachedFiles : [])
    .concat(Array.isArray(context.attached_file_contents) ? context.attached_file_contents : [])
    .concat(Array.isArray(context.attachedFileContents) ? context.attachedFileContents : []);

  return new Set(
    attached
      .map((item) => (typeof item === "string" ? item : item?.id || item?.artifactId))
      .map((value) => String(value || "").trim())
      .filter(Boolean),
  );
}

function hasProjectFileContext(context = {}) {
  const inventory = context.artifact_inventory || context.artifactInventory || {};
  const counts = inventory.counts || {};
  const workspaceFiles = context.workspace_files || context.workspaceFiles || [];
  const attached = context.attached_file_contents || context.attachedFileContents || [];
  return Boolean(
    Number(counts.spec || 0)
      || Number(counts.rtl || 0)
      || Number(counts.generated || 0)
      || (Array.isArray(workspaceFiles) && workspaceFiles.length > 0)
      || (Array.isArray(attached) && attached.length > 0)
      || context.active_file_content
      || context.activeFileContent,
  );
}

function shouldAutoReadProjectFiles(prompt, context = {}, projectId) {
  if (!projectId || isSimpleGreetingPrompt(prompt)) return false;
  if (!CODE_AGENT_PROMPT_RE.test(String(prompt || ""))) return false;
  return hasProjectFileContext(context);
}

function scoreArtifactForPrompt(file, { tokens, selectedIds, prompt }) {
  const id = String(file?.id || "");
  const type = String(file?.artifact_type || "").toLowerCase();
  const displayName = getArtifactDisplayName(file).toLowerCase();
  const filename = String(file?.filename || "").toLowerCase();
  const promptText = String(prompt || "").toLowerCase();
  let score = 0;

  if (selectedIds.has(id)) score += 120;
  if (file?.is_active) score += 80;
  if (type === "spec") score += 18;
  if (type === "rtl") score += 28;

  if (/\b(connection|connect|hierarchy|architecture|module|submodule|top|relationship)\b/i.test(promptText) && type === "rtl") {
    score += 35;
  }

  if (/\b(report|warning|error|fail|failing|pipeline|verification|mental\s+model)\b/i.test(promptText) && type === "generated") {
    score += 25;
  }

  if (/(rtl_analysis|mental_model|symbol_table|parsed_spec|verification_report|testplan)/i.test(filename)) {
    score += 18;
  }

  for (const token of tokens) {
    if (displayName.includes(token) || filename.includes(token)) {
      score += 35;
    }
  }

  return score;
}

function extractReferencedNames(content) {
  const text = String(content || "");
  const refs = new Set();

  for (const match of text.matchAll(/`include\s+["<]([^">]+)[">]/g)) {
    const name = path.basename(match[1] || "").replace(/\.[a-z0-9_]+$/i, "");
    if (name) refs.add(name.toLowerCase());
  }

  const keywords = new Set([
    "if", "else", "for", "while", "case", "assign", "always", "always_ff",
    "always_comb", "module", "endmodule", "begin", "end", "interface",
    "class", "function", "task", "generate", "initial",
  ]);

  for (const match of text.matchAll(/^\s*([a-zA-Z_][\w$]*)\s*(?:#\s*\(|[a-zA-Z_][\w$]*\s*\()/gm)) {
    const candidate = String(match[1] || "").toLowerCase();
    if (candidate && !keywords.has(candidate)) refs.add(candidate);
  }

  return refs;
}

async function postBackendJson(baseUrl, apiPath, body) {
  const response = await fetch(`${baseUrl}${apiPath}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });

  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.success) {
    throw new Error(payload?.detail || payload?.message || `Backend request failed with ${response.status}`);
  }
  return payload;
}

async function getBackendJson(baseUrl, apiPath) {
  const response = await fetch(`${baseUrl}${apiPath}`, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(payload?.detail || payload?.message || `Backend request failed with ${response.status}`);
  }
  return payload;
}

function isMentalModelChatMode(context = {}) {
  const mode = String(context.chat_mode || context.chatMode || context.mode || "").trim().toLowerCase();
  if (mode) {
    return mode === "mental_model" || mode === "mental-model" || mode === "mentalmodel";
  }
  return Boolean(context.mental_model_chat);
}

function compactMentalModelForPrompt(rawModel = {}) {
  const mentalModel = rawModel?.mental_model || rawModel || {};
  const content = mentalModel?.content || mentalModel || {};
  const design = content?.design || {};
  const projectScan = content?.project_scan || {};
  const verification = content?.verification || {};
  const blockModels = content?.block_models || {};
  const symbolTable = content?.symbol_table || {};
  const allowedSignals = new Set();
  const allowedModules = new Set();
  const allowedRequirementIds = new Set();

  function addName(set, value) {
    const name = String(value || "").trim();
    if (name) set.add(name);
  }

  function collectBlockGrounding(block = {}) {
    if (!block || typeof block !== "object") return;
    addName(allowedModules, block.top_module);
    if (Array.isArray(block.modules)) {
      for (const name of block.modules) addName(allowedModules, name);
    }
    if (Array.isArray(block.ports)) {
      for (const port of block.ports) addName(allowedSignals, port?.name);
    }
    if (Array.isArray(block.register_fields)) {
      for (const reg of block.register_fields) {
        addName(allowedSignals, reg?.name);
        addName(allowedSignals, reg?.write_enable);
        addName(allowedSignals, reg?.addr_signal);
        addName(allowedSignals, reg?.data_signal);
      }
    }
  }

  collectBlockGrounding(design);
  if (symbolTable && typeof symbolTable === "object") {
    for (const [moduleName, symbols] of Object.entries(symbolTable)) {
      addName(allowedModules, moduleName);
      if (Array.isArray(symbols)) {
        for (const symbol of symbols) addName(allowedSignals, symbol);
      }
    }
  }
  if (blockModels && typeof blockModels === "object") {
    for (const [moduleName, block] of Object.entries(blockModels)) {
      addName(allowedModules, moduleName);
      collectBlockGrounding(block);
    }
  }
  if (Array.isArray(content.requirements)) {
    for (const req of content.requirements) addName(allowedRequirementIds, req?.id);
  }

  const compactBlockModels = Object.fromEntries(
    Object.entries(blockModels || {}).slice(0, 24).map(([name, block]) => [
      name,
      {
        top_module: block?.top_module || name,
        description: block?.description,
        ports: Array.isArray(block?.ports) ? block.ports.slice(0, 32) : [],
        protocols: Array.isArray(block?.protocols) ? block.protocols.slice(0, 8) : [],
        fsms: Array.isArray(block?.fsms) ? block.fsms.slice(0, 8) : [],
        register_fields: Array.isArray(block?.register_fields) ? block.register_fields.slice(0, 16) : [],
        expected_behaviors: Array.isArray(block?.expected_behaviors) ? block.expected_behaviors.slice(0, 8) : [],
        transaction_flows: Array.isArray(block?.transaction_flows) ? block.transaction_flows.slice(0, 8) : [],
      },
    ]),
  );

  return {
    revision_metadata: {
      id: mentalModel.id,
      revision: mentalModel.revision,
      status: mentalModel.status,
      schema_version: mentalModel.schema_version,
      source_spec_artifact_id: mentalModel.source_spec_artifact_id,
      source_rtl_artifact_id: mentalModel.source_rtl_artifact_id,
      summary_text: mentalModel.summary_text,
    },
    schema_version: content.schema_version,
    status: content.status || "ready",
    design: {
      top_module: design.top_module,
      modules: Array.isArray(design.modules) ? design.modules.slice(0, 40) : [],
      hierarchy_tree: design.hierarchy_tree || {},
      ports: Array.isArray(design.ports)
        ? design.ports.slice(0, 80).map((port) => ({
          name: port?.name,
          direction: port?.direction,
          width: port?.width,
          bus_range: port?.bus_range,
          port_type: port?.port_type,
        }))
        : [],
      parameters: Array.isArray(design.parameters) ? design.parameters.slice(0, 40) : [],
      clock_domains: Array.isArray(design.clock_domains) ? design.clock_domains.slice(0, 20) : [],
      sub_instances: Array.isArray(design.sub_instances) ? design.sub_instances.slice(0, 40) : [],
      fsms: Array.isArray(design.fsms) ? design.fsms.slice(0, 20) : [],
      protocols: Array.isArray(design.protocols) ? design.protocols.slice(0, 20) : [],
      total_files: design.total_files,
      total_lines: design.total_lines,
      total_always_blocks: design.total_always_blocks,
    },
    project_scan: {
      rtl_files: Array.isArray(projectScan.rtl_files) ? projectScan.rtl_files.slice(0, 60) : [],
      spec_files: Array.isArray(projectScan.spec_files) ? projectScan.spec_files.slice(0, 20) : [],
      total_files: projectScan.total_files,
      total_lines: projectScan.total_lines,
    },
    requirements: Array.isArray(content.requirements) ? content.requirements.slice(0, 60) : [],
    verification: {
      unit_tests: Array.isArray(verification.unit_tests) ? verification.unit_tests.slice(0, 40) : [],
      formal_properties: Array.isArray(verification.formal_properties) ? verification.formal_properties.slice(0, 40) : [],
      coverage_points: Array.isArray(verification.coverage_points) ? verification.coverage_points.slice(0, 60) : [],
      uvm_scenarios: Array.isArray(verification.uvm_scenarios) ? verification.uvm_scenarios.slice(0, 40) : [],
      scoreboard_checks: Array.isArray(verification.scoreboard_checks) ? verification.scoreboard_checks.slice(0, 40) : [],
    },
    block_models: compactBlockModels,
    symbol_table: Object.fromEntries(Object.entries(symbolTable || {}).slice(0, 40)),
    grounding: {
      allowed_modules: Array.from(allowedModules).slice(0, 120),
      allowed_signals: Array.from(allowedSignals).slice(0, 240),
      allowed_requirement_ids: Array.from(allowedRequirementIds).slice(0, 120),
      rule: "Ground every design claim in these names or inspect source files before introducing a new name.",
    },
    evidence: Array.isArray(content.evidence) ? content.evidence.slice(-20) : [],
    living_agent_memory: content.living_agent?.verification_memory || {},
    risks: Array.isArray(content.risks) ? content.risks.slice(0, 40) : [],
    open_questions: Array.isArray(content.open_questions) ? content.open_questions.slice(0, 40) : [],
  };
}

async function buildMentalModelChatContext({ projectId, context = {}, options = {} }) {
  if (!projectId || !isMentalModelChatMode(context)) {
    return {};
  }

  const backendBaseUrl = normalizeBackendBaseUrl(
    options.backendBaseUrl
      || context.backendBaseUrl
      || process.env.CHIPVERIFY_BACKEND_URL,
  );

  try {
    const payload = await getBackendJson(
      backendBaseUrl,
      `/api/v1/projects/${projectId}/mental-models/latest`,
    );
    const compact = compactMentalModelForPrompt(payload);
    let serialized = JSON.stringify(compact, null, 2);
    if (serialized.length > 22000) {
      serialized = `${serialized.slice(0, 22000)}\n...<mental model context truncated>`;
    }
    return {
      mental_model_context: serialized,
      mental_model_context_loaded: true,
      mental_model_revision_id:
        compact.revision_metadata?.id || context.mental_model_revision_id || null,
      mental_model_revision:
        compact.revision_metadata?.revision || context.mental_model_revision || null,
    };
  } catch (error) {
    return {
      mental_model_context_loaded: false,
      mental_model_context_error: error?.message || "Mental model context unavailable.",
    };
  }
}

function selectAutoReadArtifacts(files, { prompt, context }) {
  const tokens = promptTokens(prompt);
  const selectedIds = getVerificationContextIds(context);
  const attachedIds = getAttachedArtifactIds(context);
  const promptText = String(prompt || "");
  const rtlFiles = files.filter((file) => String(file?.artifact_type || "").toLowerCase() === "rtl");
  const readAllRtlForSmallDesign =
    rtlFiles.length > 1
    && rtlFiles.length <= 8
    && /\b(connection|connect|hierarchy|architecture|module|submodule|top|relationship|design)\b/i.test(promptText);

  return files
    .map((file) => {
      const id = String(file?.id || "");
      const score = scoreArtifactForPrompt(file, { tokens, selectedIds, prompt });
      const forceSmallRtl = readAllRtlForSmallDesign
        && String(file?.artifact_type || "").toLowerCase() === "rtl";
      return { file, score: forceSmallRtl ? Math.max(score, 75) : score };
    })
    .filter(({ file, score }) => {
      const id = String(file?.id || "");
      return id && !attachedIds.has(id) && (score > 0 || selectedIds.has(id));
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, AUTO_READ_MAX_FILES)
    .map(({ file }) => file);
}

async function buildAutoReadProjectContext({ prompt, projectId, context = {}, options = {} }) {
  if (!shouldAutoReadProjectFiles(prompt, context, projectId)) {
    return {};
  }

  const backendBaseUrl = normalizeBackendBaseUrl(
    options.backendBaseUrl
      || context.backendBaseUrl
      || process.env.CHIPVERIFY_BACKEND_URL,
  );

  try {
    const listPayload = await postBackendJson(
      backendBaseUrl,
      "/api/v1/tools/listFiles",
      { project_id: projectId },
    );
    const files = Array.isArray(listPayload?.files) ? listPayload.files : [];
    const selected = selectAutoReadArtifacts(files, { prompt, context });
    const verificationIds = getVerificationContextIds(context);
    const loaded = [];
    let totalChars = 0;

    async function readOne(file, reason = "prompt relevance") {
      const id = String(file?.id || "").trim();
      if (!id || totalChars >= AUTO_READ_MAX_TOTAL_CHARS) return null;
      if (loaded.some((item) => item.id === id)) return null;

      const readPayload = await postBackendJson(
        backendBaseUrl,
        "/api/v1/tools/readFile",
        { artifact_id: id },
      );
      const rawContent = String(readPayload?.content || "");
      const remaining = AUTO_READ_MAX_TOTAL_CHARS - totalChars;
      const maxForFile = Math.max(0, Math.min(AUTO_READ_MAX_FILE_CHARS, remaining));
      if (!rawContent || maxForFile <= 0) return null;

      const content = rawContent.length > maxForFile
        ? `${rawContent.slice(0, maxForFile)}\n...[truncated ${rawContent.length - maxForFile} chars by auto-read context]`
        : rawContent;
      totalChars += content.length;

      const entry = {
        id,
        filename: readPayload?.filename || file?.filename || "artifact",
        artifact_type: readPayload?.artifact_type || file?.artifact_type || "unknown",
        relative_path: readPayload?.relative_path || getArtifactDisplayName(file),
        reason,
        content,
      };
      loaded.push(entry);
      return entry;
    }

    const initialReads = [];
    for (const file of selected) {
      try {
        const entry = await readOne(file, verificationIds.has(String(file?.id || "")) ? "selected/default verification file" : "prompt relevance");
        if (entry) initialReads.push(entry);
      } catch {
        // Keep reading other files; this context is best-effort.
      }
    }

    const references = new Set();
    for (const entry of initialReads) {
      for (const ref of extractReferencedNames(entry.content)) references.add(ref);
    }

    if (references.size > 0 && loaded.length < AUTO_READ_MAX_FILES) {
      const attachedIds = getAttachedArtifactIds(context);
      const referencedFiles = files
        .filter((file) => {
          const id = String(file?.id || "");
          if (!id || attachedIds.has(id) || loaded.some((item) => item.id === id)) return false;
          const type = String(file?.artifact_type || "").toLowerCase();
          if (type !== "rtl" && type !== "generated") return false;
          const displayName = getArtifactDisplayName(file).toLowerCase();
          const base = path.basename(displayName).replace(/\.[a-z0-9_]+$/i, "");
          return references.has(base) || Array.from(references).some((ref) => displayName.includes(ref));
        })
        .slice(0, AUTO_READ_MAX_FILES - loaded.length);

      for (const file of referencedFiles) {
        try {
          await readOne(file, "referenced by selected RTL/spec");
        } catch {
          // Best-effort follow-up read.
        }
      }
    }

    return {
      auto_read_project: {
        enabled: true,
        total_artifacts: files.length,
        loaded_count: loaded.length,
        loaded_files: loaded.map((item) => ({
          id: item.id,
          filename: item.filename,
          artifact_type: item.artifact_type,
          relative_path: item.relative_path,
          reason: item.reason,
        })),
      },
      auto_read_file_contents: loaded,
    };
  } catch (error) {
    logDebug(
      "runtime.auto_read.failed",
      {
        projectId,
        message: error?.message || String(error),
      },
      {
        component: "runtime",
        workspaceRoot: context.workspaceRoot,
      },
    );
    return {
      auto_read_project: {
        enabled: false,
        error: error?.message || String(error),
      },
    };
  }
}

// ── Core initialization ────────────────────────────────────────────────────
function buildCodeAgentModeInstructions({ prompt, context = {}, projectId }) {
  if (!shouldAutoReadProjectFiles(prompt, context, projectId)) {
    return "";
  }

  return `

## Code Agent Work Mode
This user request is about the current project. Work like Claude Code:
1. Use the attached, active, and auto-read files as the first source of truth.
2. If the answer depends on a file that is not in context, inspect it with tools before answering:
   - backend artifacts: list_project_artifacts, then read_project_artifact
   - local workspace files: workspace_list_files, workspace_grep, then workspace_read_file
3. For design/RTL questions, trace connections: spec intent -> top RTL/module -> submodules/includes/interfaces -> generated tests/reports when relevant.
4. Do not say "I do not have context" while project artifacts, active file content, or auto-read files are present.
5. In the final answer, briefly name the files you used and explain the relationships you found.`;
}

async function initializeRuntime(options = {}) {
  const workspaceRoot = resolveWorkspaceRoot(options.workspaceRoot);

  ensureRuntimeEnvLoaded(workspaceRoot);

  logDebug(
    "runtime.initialize.start",
    {
      workspaceRoot,
      model: options.model || process.env.CHIPVERIFY_MASTRA_MODEL || process.env.CHIPVERIFY_MODEL || null,
      backendBaseUrl: options.backendBaseUrl || process.env.CHIPVERIFY_BACKEND_URL || null,
    },
    {
      component: "runtime",
      workspaceRoot,
    },
  );

  console.info("[Mastra Runtime] Initializing runtime for workspace:", workspaceRoot);

  // Memory — LibSQL persistent, survives restarts
  const memory = await createProjectScopedMemory();

  // Backend bridge tools — call Python FastAPI backend
  const backendTools = await createBackendBridgeTools({
    backendBaseUrl: options.backendBaseUrl,
    authToken: options.backendAuthToken,
  });

  // LOCAL FILESYSTEM TOOLS — these are the critical ones
  // bash tool: real shell access via just-bash + ReadWriteFs
  const bashTool = await createWorkspaceBashTool({ workspaceRoot });

  // Structured filesystem tools (list/read/write/grep with schema)
  const fsTool = await createWorkspaceFilesystemTools({ workspaceRoot });

  // Combine local tools into one object
  const localTools = {
    bash: bashTool,
    workspace_list_files: fsTool.listFiles,
    workspace_read_file: fsTool.readFile,
    workspace_write_file: fsTool.writeFile,
    workspace_grep: fsTool.grepFiles,
  };

  // Agent — gets ALL tools: local filesystem + backend bridge
  const agent = await createWorkspaceCopilotAgent({
    memory,
    model: options.model,
    backendTools,
    localTools,
  });

  // Mastra instance (for observability/tracing)
  let mastra = null;
  try {
    const mastraModule = await import("@mastra/core/mastra");
    const Mastra = mastraModule.Mastra || mastraModule.default?.Mastra || mastraModule.default;
    mastra = new Mastra({
      agents: { workspaceCopilot: agent },
    });
  } catch (err) {
    console.warn("[Mastra Runtime] Could not create Mastra instance:", err?.message);
  }

  console.info("[Mastra Runtime] Ready — tools:", Object.keys(localTools).concat(Object.keys(backendTools)));

  logDebug(
    "runtime.initialize.ready",
    {
      workspaceRoot,
      localTools: Object.keys(localTools),
      backendTools: Object.keys(backendTools),
      hasMastraInstance: Boolean(mastra),
    },
    {
      component: "runtime",
      workspaceRoot,
    },
  );

  return {
    mastra,
    memory,
    agent,
    backendTools,
    localTools,
    workspaceRoot,
  };
}

async function getRuntime(options = {}) {
  const workspaceRoot = resolveWorkspaceRoot(options.workspaceRoot);

  if (runtimeCache.has(workspaceRoot)) {
    return runtimeCache.get(workspaceRoot);
  }

  const promise = initializeRuntime({ ...options, workspaceRoot });
  runtimeCache.set(workspaceRoot, promise);

  // If no default is set, make this the default
  if (!defaultRuntimePromise) {
    defaultRuntimePromise = promise;
  }

  return promise;
}

// ── Context injection ──────────────────────────────────────────────────────
/**
 * Build a context string to append to the user's prompt.
 * Gives the agent the workspace root, project ID, active file, etc.
 */
function buildContextString(context = {}) {
  const parts = [];
  const workspaceRoot = resolveWorkspaceRoot(context.workspaceRoot);
  const runDetails = context.run_details || context.runDetails || null;
  const artifactInventory = context.artifact_inventory || context.artifactInventory || null;
  const verificationContext = context.verification_context || context.verificationContext || null;
  const workspaceFiles = Array.isArray(context.workspace_files || context.workspaceFiles)
    ? (context.workspace_files || context.workspaceFiles)
    : [];

  parts.push(`## Workspace Context`);
  parts.push(`Workspace root: ${workspaceRoot}`);

  if (context.project_id || context.projectId) {
    parts.push(`Project ID: ${context.project_id || context.projectId}`);
    parts.push(
      "Note: project revisions are stored under artifact and run folders, so use artifact metadata and run summaries before assuming the workspace root is empty.",
    );
  }
  if (context.project_name || context.projectName) {
    parts.push(`Project name: ${context.project_name || context.projectName}`);
  }
  if (isMentalModelChatMode(context)) {
    parts.push(
      [
        "## Mental Model Chat Mode",
        "The user selected Mental Model Chat. Answer as the design mental model, using the persisted mental model below as the first source of truth.",
        "Do not invent behavior, protocols, requirements, coverage, bugs, or UVM structure that is not present in the model or inspected source files.",
        "Ground every signal/module/requirement claim against grounding.allowed_signals, grounding.allowed_modules, and grounding.allowed_requirement_ids, or inspect source files with tools before using a new name.",
        "If a requested signal or behavior is absent from the allowed grounding lists and source context, say it is not captured instead of guessing.",
        "If a detail is missing from the model, say it is not captured and suggest rebuilding/refining the mental model or inspecting the relevant file.",
        `Mental model status: ${context.mental_model_status || context.mentalModelStatus || "unknown"}`,
        `Mental model revision id: ${context.mental_model_revision_id || context.mentalModelRevisionId || "unknown"}`,
      ].join("\n"),
    );
    if (context.mental_model_context_loaded && context.mental_model_context) {
      parts.push(`\n## Persisted Mental Model\n\`\`\`json\n${context.mental_model_context}\n\`\`\``);
    } else if (context.mental_model_context_error) {
      parts.push(`Mental model context load error: ${context.mental_model_context_error}`);
    }
  }
  if (context.active_file_name || context.activeFileName) {
    parts.push(`Active file: ${context.active_file_name || context.activeFileName}`);
  }
  if (context.active_file_type || context.active_artifact_type || context.activeArtifactType) {
    parts.push(`Active file type: ${context.active_file_type || context.active_artifact_type || context.activeArtifactType}`);
  }
  const activeFileContent = context.active_file_content || context.activeFileContent || "";
  if (typeof activeFileContent === "string" && activeFileContent.trim()) {
    const activeFileName = context.active_file_name || context.activeFileName || "active file";
    parts.push(
      `\n## Active File Content\n### ${activeFileName}\n\`\`\`\n${activeFileContent.slice(0, 6000)}\n\`\`\``,
    );
  }

  if (context.run_id || context.runId || context.run_status || context.runStatus || runDetails?.status) {
    const runId = context.run_id || context.runId || "unknown";
    const runStatus = context.run_status || context.runStatus || runDetails?.status || "unknown";
    parts.push(`Active run: ${runId} (${runStatus})`);
  }

  if (Array.isArray(runDetails?.recent_events) && runDetails.recent_events.length > 0) {
    const recentEvents = runDetails.recent_events
      .slice(-10)
      .map((entry, index) => `${index + 1}. ${String(entry || "").trim()}`)
      .join("\n");
    if (recentEvents) {
      parts.push(`Recent run events:\n${recentEvents}`);
    }
  }

  if (artifactInventory) {
    const counts = artifactInventory.counts || {};
    parts.push(
      `Artifact inventory: spec=${Number(counts.spec || 0)}, rtl=${Number(counts.rtl || 0)}, generated=${Number(counts.generated || 0)}`,
    );

    const specFiles = Array.isArray(artifactInventory.spec) ? artifactInventory.spec.slice(0, 10) : [];
    const rtlFiles = Array.isArray(artifactInventory.rtl) ? artifactInventory.rtl.slice(0, 12) : [];
    const generatedFiles = Array.isArray(artifactInventory.generated) ? artifactInventory.generated.slice(0, 16) : [];

    if (specFiles.length > 0) {
      parts.push(`Spec files: ${specFiles.join(", ")}`);
    }
    if (rtlFiles.length > 0) {
      parts.push(`RTL files: ${rtlFiles.join(", ")}`);
    }
    if (generatedFiles.length > 0) {
      parts.push(`Generated files: ${generatedFiles.join(", ")}`);
    }
  }

  if (verificationContext) {
    const availableSpecFiles = Array.isArray(verificationContext.available_spec_files)
      ? verificationContext.available_spec_files
        .map((file) => file?.filename || file?.id || "")
        .filter(Boolean)
        .slice(0, 10)
      : [];
    const availableRtlFiles = Array.isArray(verificationContext.available_rtl_files)
      ? verificationContext.available_rtl_files
        .map((file) => file?.filename || file?.id || "")
        .filter(Boolean)
        .slice(0, 12)
      : [];

    parts.push(
      `Verification readiness: spec=${verificationContext.has_spec ? "ready" : "missing"}, rtl=${verificationContext.has_rtl ? "ready" : "missing"}, prompt_spec=${verificationContext.has_prompt_spec ? "yes" : "no"}`,
    );

    const selectedSpec = verificationContext.active_spec || verificationContext.activeSpec || null;
    const selectedRtl = verificationContext.active_rtl || verificationContext.activeRtl || null;
    const selectedSpecId = verificationContext.selected_spec_id || verificationContext.selectedSpecId || selectedSpec?.id || "";
    const selectedRtlId = verificationContext.selected_rtl_id || verificationContext.selectedRtlId || selectedRtl?.id || "";
    const selectedSpecName = selectedSpec?.filename || selectedSpec?.relative_path || "";
    const selectedRtlName = selectedRtl?.filename || selectedRtl?.relative_path || "";

    if (selectedSpecId || selectedSpecName) {
      parts.push(`Selected verification spec: ${selectedSpecName || "unknown"}${selectedSpecId ? ` (${selectedSpecId})` : ""}`);
    }
    if (selectedRtlId || selectedRtlName) {
      parts.push(`Selected verification RTL: ${selectedRtlName || "unknown"}${selectedRtlId ? ` (${selectedRtlId})` : ""}`);
    }
    if (verificationContext.has_spec || verificationContext.has_rtl) {
      parts.push(
        "Design context rule: questions like 'explain the design' refer to the selected/default spec and RTL above unless the user says otherwise.",
      );
    }

    if (availableSpecFiles.length > 0) {
      parts.push(`Available verification spec files: ${availableSpecFiles.join(", ")}`);
    }
    if (availableRtlFiles.length > 0) {
      parts.push(`Available verification RTL files: ${availableRtlFiles.join(", ")}`);
    }
  }

  if (workspaceFiles.length > 0) {
    parts.push(`Known workspace files: ${workspaceFiles.slice(0, 40).join(", ")}`);
  }

  const autoReadProject = context.auto_read_project || context.autoReadProject || null;
  if (autoReadProject?.enabled) {
    const loadedFiles = Array.isArray(autoReadProject.loaded_files)
      ? autoReadProject.loaded_files
      : [];
    parts.push(
      `Code-agent auto-read: loaded ${Number(autoReadProject.loaded_count || loadedFiles.length || 0)} relevant artifact(s) from ${Number(autoReadProject.total_artifacts || 0)} total artifact(s).`,
    );
    if (loadedFiles.length > 0) {
      const loadedSummary = loadedFiles
        .slice(0, 12)
        .map((file) => `${file.filename || file.relative_path || file.id} [${file.artifact_type || "unknown"}; ${file.reason || "context"}]`)
        .join(", ");
      parts.push(`Auto-read files: ${loadedSummary}`);
    }
  } else if (autoReadProject?.error) {
    parts.push(`Code-agent auto-read attempted but failed: ${autoReadProject.error}`);
  }

  // Attached file snippets (from user selecting files in the UI)
  if (context.attached_file_contents || context.attachedFileContents) {
    const attached = context.attached_file_contents || context.attachedFileContents;
    if (Array.isArray(attached) && attached.length > 0) {
      const snippets = attached
        .slice(0, 5)
        .map((item) => {
          if (!item) return null;
          const name = item.filename || item.id || "file";
          const content = String(item.content || "").slice(0, 4000);
          return content ? `### ${name}\n\`\`\`\n${content}\n\`\`\`` : null;
        })
        .filter(Boolean)
        .join("\n\n");
      if (snippets) parts.push(`\n## Attached Files\n${snippets}`);
    }
  }

  const autoReadFiles = context.auto_read_file_contents || context.autoReadFileContents;
  if (Array.isArray(autoReadFiles) && autoReadFiles.length > 0) {
    const snippets = autoReadFiles
      .slice(0, 10)
      .map((item) => {
        if (!item) return null;
        const name = item.relative_path || item.filename || item.id || "file";
        const reason = item.reason ? ` (${item.reason})` : "";
        const content = String(item.content || "").slice(0, 7000);
        return content ? `### ${name}${reason}\n\`\`\`\n${content}\n\`\`\`` : null;
      })
      .filter(Boolean)
      .join("\n\n");
    if (snippets) {
      parts.push(`\n## Auto-Read Project Files\nThese files were preloaded because the user asked a project/code question. Treat them as file evidence, and use artifact tools if more files are needed.\n\n${snippets}`);
    }
  }

  return `\n\n${parts.join("\n")}`;
}

// ── Typed stream processor ─────────────────────────────────────────────────
async function streamFromMastraOutput({ stream, runId, onEvent }) {
  let suspended = false;
  let unknownPartCount = 0;

  function extractTextFromPart(part = {}) {
    const payload = (part && typeof part.payload === "object") ? part.payload : {};
    const data = (part && typeof part.data === "object") ? part.data : {};

    const direct = [
      part.text,
      part.textDelta,
      part.text_delta,
      part.delta,
      part.output,
      part.outputText,
      part.output_text,
      part.content,
      part.value,
      part?.message?.content,
      part?.response?.output_text,
      payload.text,
      payload.textDelta,
      payload.text_delta,
      payload.delta,
      payload.output,
      payload.outputText,
      payload.output_text,
      payload.content,
      payload.value,
      payload?.message?.content,
      payload?.response?.output_text,
      data.text,
      data.textDelta,
      data.text_delta,
      data.delta,
      data.output,
      data.outputText,
      data.output_text,
      data.content,
      data.value,
      data?.message?.content,
      data?.response?.output_text,
    ];

    for (const candidate of direct) {
      if (typeof candidate === "string" && candidate.trim()) {
        return candidate;
      }
    }

    const arrayCandidates = [part?.content, payload?.content, data?.content];
    for (const content of arrayCandidates) {
      if (!Array.isArray(content)) continue;
      const joined = content
        .map((item) => {
          if (typeof item === "string") return item;
          if (typeof item?.text === "string") return item.text;
          if (typeof item?.content === "string") return item.content;
          return "";
        })
        .filter(Boolean)
        .join("");

      if (joined.trim()) return joined;
    }

    return "";
  }

  function sortToolValue(value) {
    if (Array.isArray(value)) {
      return value.map(sortToolValue);
    }

    if (value && typeof value === "object") {
      return Object.keys(value)
        .sort()
        .reduce((acc, key) => {
          acc[key] = sortToolValue(value[key]);
          return acc;
        }, {});
    }

    return value;
  }

  function stableToolJson(value) {
    try {
      return JSON.stringify(sortToolValue(value ?? null));
    } catch {
      return JSON.stringify(String(value ?? ""));
    }
  }

  function hashToolText(value = "") {
    let hash = 0;
    const text = String(value || "");

    for (let index = 0; index < text.length; index += 1) {
      hash = ((hash * 31) + text.charCodeAt(index)) >>> 0;
    }

    return hash.toString(16);
  }

  function extractToolCall(part = {}) {
    const payload = (part && typeof part.payload === "object") ? part.payload : {};
    const data = (part && typeof part.data === "object") ? part.data : {};
    const toolName = part.toolName || payload.toolName || data.toolName || "tool";
    const args = part.args || payload.args || data.args || {};
    const explicitCallId = part.toolCallId || payload.toolCallId || data.toolCallId || null;
    const syntheticCallId = `tool_${hashToolText(`${toolName}:${stableToolJson(args)}`)}`;

    return {
      callId: explicitCallId || syntheticCallId,
      tool: toolName,
      name: toolName,
      args,
      result: part.result ?? payload.result ?? data.result ?? part.output ?? payload.output ?? data.output ?? null,
    };
  }

  if (stream?.fullStream && Symbol.asyncIterator in stream.fullStream) {
    for await (const part of stream.fullStream) {
      let partType = "";
      try {
        partType = String(part?.type || "").toLowerCase();
      } catch {
        partType = "";
      }

      try {
        switch (partType) {
          case "start":
          case "step-start":
          case "step_end":
          case "step-end":
          case "text-start":
          case "text-end":
          case "tool-call-input-streaming-start":
          case "tool-call-input-streaming-end":
          case "tool-call-delta":
          case "data-om-status":
          case "status":
          case "run-started":
          case "run-start":
            break;

          case "text-delta":
          case "text_delta":
          case "response.output_text.delta":
          case "output_text_delta":
          case "output-text-delta":
          case "text": {
            const delta = extractTextFromPart(part);
            if (delta) onEvent?.({ type: "text_delta", runId, text: String(delta) });
            break;
          }

          case "tool-call":
          case "tool_call": {
            const toolCall = extractToolCall(part);
            onEvent?.({
              type: "tool_call_update",
              runId,
              toolCall: {
                callId: toolCall.callId,
                tool: toolCall.tool,
                name: toolCall.name,
                args: toolCall.args,
                result: null,
                status: "input-available",
                state: "input-available",
              },
            });
            break;
          }

          case "tool-result":
          case "tool_result": {
            const toolCall = extractToolCall(part);
            const isError = part.isError === true;
            onEvent?.({
              type: "tool_call_update",
              runId,
              toolCall: {
                callId: toolCall.callId,
                tool: toolCall.tool,
                name: toolCall.name,
                args: toolCall.args,
                result: toolCall.result,
                output: toolCall.result,
                status: isError ? "error" : "output-available",
                state: isError ? "error" : "output-available",
              },
            });
            break;
          }

          case "tool-call-approval":
          case "tool_call_approval":
          case "tool-call-suspended":
          case "tool_call_suspended": {
            const toolCall = extractToolCall(part);
            suspended = true;
            onEvent?.({
              type: "tool_call_update",
              runId,
              toolCall: {
                callId: toolCall.callId,
                tool: toolCall.tool,
                name: toolCall.name,
                args: toolCall.args,
                result: null,
                status: "awaiting_confirmation",
                state: "awaiting_confirmation",
              },
            });
            break;
          }

          case "error": {
            const errMsg = part.error?.message || String(part.error || "stream error");
            onEvent?.({ type: "run_error", runId, message: errMsg });
            break;
          }

          case "finish":
          case "finish-step":
          case "step-finish":
            break;

          default: {
            const fallbackText = extractTextFromPart(part);
            if (fallbackText) {
              onEvent?.({ type: "text_delta", runId, text: String(fallbackText) });
            } else if (unknownPartCount < 5) {
              unknownPartCount += 1;
              logDebug(
                "runtime.stream.unknown_part",
                {
                  runId,
                  partType: partType || null,
                  keys: part && typeof part === "object" ? Object.keys(part).slice(0, 25) : [],
                },
                { component: "runtime", runId },
              );
            }
            break;
          }
        }
      } catch (partError) {
        logDebug(
          "runtime.stream.part_parse_error",
          {
            runId,
            partType: partType || null,
            message: partError?.message || String(partError),
          },
          { component: "runtime", runId },
        );
      }
    }
  } else if (stream?.textStream && Symbol.asyncIterator in stream.textStream) {
    for await (const text of stream.textStream) {
      if (text) onEvent?.({ type: "text_delta", runId, text: String(text) });
    }
  } else {
    const fallbackText = typeof stream?.text === "string" ? stream.text : "";
    if (fallbackText) onEvent?.({ type: "text_delta", runId, text: fallbackText });
  }

  return { suspended };
}

// ── Main stream entry point ────────────────────────────────────────────────
async function streamCopilotResponse({
  prompt,
  projectId,
  threadId,
  userId,
  context = {},
  onEvent,
  options = {},
}) {
  const { agent } = await getRuntime({
    ...options,
    workspaceRoot: context.workspaceRoot,
  });

  const runId = randomUUID();
  const memoryScope = buildMemoryScope({ projectId, threadId, userId });

  // Enrich the prompt with workspace context and a small, best-effort source pack
  // for project-grounded questions.
  const autoReadContext = await buildAutoReadProjectContext({
    prompt,
    projectId,
    context,
    options,
  });
  const mentalModelContext = await buildMentalModelChatContext({
    projectId,
    context,
    options,
  });
  const enrichedContext = {
    ...context,
    ...autoReadContext,
    ...mentalModelContext,
    projectId,
    project_id: projectId,
  };
  const codeAgentInstructions = buildCodeAgentModeInstructions({
    prompt,
    context: enrichedContext,
    projectId,
  });
  const contextStr = buildContextString(enrichedContext);
  const enrichedPrompt = `${prompt}${codeAgentInstructions}${contextStr}`;

  logDebug(
    "runtime.stream.start",
    {
      prompt,
      enrichedPrompt,
      memoryScope,
      context: enrichedContext,
      projectId,
      threadId,
      userId,
    },
    {
      component: "runtime",
      workspaceRoot: context.workspaceRoot,
      runId,
    },
  );

  const stream = await agent.stream(enrichedPrompt, { memory: memoryScope });
  const mastraRunId = stream?.runId || runId;

  onEvent?.({ type: "run_started", runId: mastraRunId, traceId: stream?.traceId || null });

  const streamState = await streamFromMastraOutput({ stream, runId: mastraRunId, onEvent });

  onEvent?.({ type: "run_completed", runId: mastraRunId });

  logDebug(
    "runtime.stream.completed",
    {
      runId: mastraRunId,
      traceId: stream?.traceId || null,
      suspended: Boolean(streamState?.suspended),
    },
    {
      component: "runtime",
      workspaceRoot: context.workspaceRoot,
      runId: mastraRunId,
    },
  );

  return {
    runId: mastraRunId,
    traceId: stream?.traceId || null,
    suspended: Boolean(streamState?.suspended),
  };
}

// ── Tool approval / decline ────────────────────────────────────────────────
async function approveCopilotToolCall({ runId, toolCallId, projectId, threadId, userId, context = {}, onEvent, options = {} }) {
  const { agent } = await getRuntime({ ...options, workspaceRoot: context.workspaceRoot });
  const memoryScope = buildMemoryScope({ projectId, threadId, userId });
  const stream = await agent.approveToolCall({ runId, toolCallId, memory: memoryScope });
  const mastraRunId = stream?.runId || runId;
  const streamState = await streamFromMastraOutput({ stream, runId: mastraRunId, onEvent });
  onEvent?.({ type: "run_completed", runId: mastraRunId });
  return { runId: mastraRunId, traceId: stream?.traceId || null, suspended: Boolean(streamState?.suspended) };
}

async function declineCopilotToolCall({ runId, toolCallId, projectId, threadId, userId, context = {}, onEvent, options = {} }) {
  const { agent } = await getRuntime({ ...options, workspaceRoot: context.workspaceRoot });
  const memoryScope = buildMemoryScope({ projectId, threadId, userId });
  const stream = await agent.declineToolCall({ runId, toolCallId, memory: memoryScope });
  const mastraRunId = stream?.runId || runId;
  const streamState = await streamFromMastraOutput({ stream, runId: mastraRunId, onEvent });
  onEvent?.({ type: "run_completed", runId: mastraRunId });
  return { runId: mastraRunId, traceId: stream?.traceId || null, suspended: Boolean(streamState?.suspended) };
}

module.exports = {
  getRuntime,
  resolveWorkspaceRoot,
  streamCopilotResponse,
  approveCopilotToolCall,
  declineCopilotToolCall,
};
