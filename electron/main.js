const { app, BrowserWindow, ipcMain, Menu, dialog, safeStorage, shell } = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const crypto = require("node:crypto");
const { spawn, spawnSync } = require("node:child_process");
const { registerMastraCopilotIpc } = require("./mastra/ipc/copilot");
const {
  activateMachine,
  getActivationStatus,
  getLicenseSecret,
  getMachineId,
} = require("./security/activation");
const { startAutoUpdateOnLaunch, scheduleAutoUpdateCheck } = require("./updater");
const {
  initElectronSentry,
  captureElectronException,
  captureElectronMessage,
  readLogTail,
} = require("./observability/sentry");

let mainWindow = null;
let managedBackendProcess = null;
let runtimeEnvLoaded = false;
let cloudEntitlementState = null;
let managedBackendDiagnostics = {
  executablePath: "",
  cwd: "",
  backendUrl: "",
  backendLogPath: "",
  label: "",
  startedAt: "",
  exitedAt: "",
  exitCode: null,
  signal: null,
  error: "",
};

const DEFAULT_BACKEND_URL = "http://127.0.0.1:7348";
const DEFAULT_OPENAI_MODEL = "gpt-5.4";
const DEMO_GEMINI_API_KEY = process.env.CHIPVERIFY_DEMO_GEMINI_API_KEY || "";
const DEMO_GEMINI_MODEL = "gemini-2.5-pro";

// Alpha-only Bedrock defaults for packaged Linux/desktop builds.
// Remove before public release — key is embedded in the installer artifact.
const PACKAGED_BEDROCK_REGION = "us-east-1";
const PACKAGED_BEDROCK_MODEL = "deepseek.v3.2";
const PACKAGED_BEDROCK_API_BASE = "https://bedrock-mantle.us-east-1.api.aws/v1";
const PACKAGED_BEDROCK_API_KEY =
  process.env.CHIPVERIFY_DEMO_BEDROCK_API_KEY || process.env.BEDROCK_API_KEY || "";

function parseEnvValue(rawValue) {
  const trimmed = rawValue.trim();
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
    console.warn("[Electron] Failed to load env file", { filePath, message: error?.message });
    return false;
  }
}

function normalizeModelProviderEnvAliases() {
  if (!process.env.GOOGLE_GENERATIVE_AI_API_KEY && process.env.GOOGLE_API_KEY) {
    process.env.GOOGLE_GENERATIVE_AI_API_KEY = process.env.GOOGLE_API_KEY;
  }
}

function applyDemoGeminiDefaults() {
  const forceGemini = boolFromEnv(process.env.CHIPVERIFY_DEMO_FORCE_GEMINI);
  const shouldUseDemoDefaults = forceGemini === true;

  if (shouldUseDemoDefaults) {
    process.env.CHIPVERIFY_LLM_PROVIDER = "gemini";
    process.env.MODEL_PROVIDER = "gemini";
    process.env.MODEL_NAME = DEMO_GEMINI_MODEL;
    process.env.GEMINI_MODEL = DEMO_GEMINI_MODEL;
    process.env.CHIPVERIFY_LLM_API_KEY_REQUIRED = "false";
  }

  if (
    shouldUseDemoDefaults
    && DEMO_GEMINI_API_KEY
    && !process.env.GEMINI_API_KEY
    && !process.env.GOOGLE_API_KEY
    && !process.env.GOOGLE_GENERATIVE_AI_API_KEY
  ) {
    process.env.GEMINI_API_KEY = DEMO_GEMINI_API_KEY;
    process.env.GOOGLE_API_KEY = DEMO_GEMINI_API_KEY;
    process.env.GOOGLE_GENERATIVE_AI_API_KEY = DEMO_GEMINI_API_KEY;
  }
}

function applyPackagedBedrockDefaults() {
  if (!app.isPackaged) {
    return;
  }

  const provider = String(process.env.CHIPVERIFY_LLM_PROVIDER || process.env.MODEL_PROVIDER || "")
    .trim()
    .toLowerCase()
    .replace(/-/g, "_");
  if (provider && provider !== "bedrock") {
    return;
  }

  process.env.CHIPVERIFY_CLOUD_CONTROL_DISABLED = "true";
  process.env.CHIPVERIFY_LLM_PROVIDER = "bedrock";
  process.env.MODEL_PROVIDER = "bedrock";
  process.env.BEDROCK_REGION = PACKAGED_BEDROCK_REGION;
  process.env.BEDROCK_MODEL = PACKAGED_BEDROCK_MODEL;
  process.env.MODEL_NAME = PACKAGED_BEDROCK_MODEL;
  process.env.CHIPVERIFY_LLM_MODEL_ALIAS = PACKAGED_BEDROCK_MODEL;
  process.env.BEDROCK_API_BASE = PACKAGED_BEDROCK_API_BASE;
  process.env.CHIPVERIFY_LLM_BASE_URL = PACKAGED_BEDROCK_API_BASE;
  process.env.BEDROCK_API_KEY = process.env.BEDROCK_API_KEY || PACKAGED_BEDROCK_API_KEY;
  process.env.AWS_BEARER_TOKEN_BEDROCK = process.env.AWS_BEARER_TOKEN_BEDROCK || process.env.BEDROCK_API_KEY;
  process.env.CHIPVERIFY_COMPLETION_PROVIDER = "bedrock";
  process.env.BEDROCK_COMPLETION_MODEL = PACKAGED_BEDROCK_MODEL;
  process.env.CHIPVERIFY_BEDROCK_COMPLETION_MODEL = PACKAGED_BEDROCK_MODEL;
  process.env.CHIPVERIFY_COMPLETION_MODEL = PACKAGED_BEDROCK_MODEL;
  process.env.CHIPVERIFY_DEMO_FORCE_GEMINI = "false";
  process.env.CHIPVERIFY_LLM_API_KEY_REQUIRED = "false";
}

function applyPackagedOpenAiDefaults() {
  if (!app.isPackaged) {
    return;
  }
  if (shouldUseCloudControl()) {
    return;
  }
  const provider = String(process.env.CHIPVERIFY_LLM_PROVIDER || process.env.MODEL_PROVIDER || "")
    .trim()
    .toLowerCase()
    .replace(/-/g, "_");
  if (provider && provider !== "openai_sdk" && provider !== "chatgpt" && provider !== "bedrock") {
    return;
  }
  if (provider === "bedrock") {
    return;
  }

  process.env.CHIPVERIFY_LLM_PROVIDER = "openai";
  process.env.MODEL_PROVIDER = "openai";
  const currentModel = String(process.env.OPENAI_MODEL || process.env.MODEL_NAME || "").trim();
  const model = !currentModel || currentModel.toLowerCase().includes("gemini")
    ? DEFAULT_OPENAI_MODEL
    : currentModel;
  process.env.MODEL_NAME = model;
  process.env.OPENAI_MODEL = model;
  const currentAlias = String(process.env.CHIPVERIFY_LLM_MODEL_ALIAS || "").trim();
  process.env.CHIPVERIFY_LLM_MODEL_ALIAS =
    !currentAlias || currentAlias.toLowerCase().includes("gemini") ? model : currentAlias;
  process.env.OPENAI_API_BASE = process.env.OPENAI_API_BASE || "https://api.openai.com/v1";
  process.env.OPENAI_BASE_URL = process.env.OPENAI_BASE_URL || process.env.OPENAI_API_BASE;
  process.env.CHIPVERIFY_LLM_BASE_URL = process.env.CHIPVERIFY_LLM_BASE_URL || process.env.OPENAI_API_BASE;
  process.env.CHIPVERIFY_LLM_API_KEY_REQUIRED = process.env.CHIPVERIFY_LLM_API_KEY_REQUIRED || "false";
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

function resolveRepoRoot() {
  return path.join(__dirname, "..");
}

function resolveAppContentRoot() {
  return app.isPackaged ? app.getAppPath() : resolveRepoRoot();
}

function resolveResourceRoot() {
  return app.isPackaged ? process.resourcesPath : resolveRepoRoot();
}

function resolveWorkspaceRoot() {
  if (process.env.CHIPVERIFY_WORKSPACE_ROOT) {
    return path.resolve(process.env.CHIPVERIFY_WORKSPACE_ROOT);
  }
  if (app.isPackaged) {
    return path.join(app.getPath("userData"), "workspace");
  }
  return resolveRepoRoot();
}

const APP_CONTENT_ROOT = resolveAppContentRoot();
const RESOURCE_ROOT = resolveResourceRoot();
const PROJECT_ROOT = resolveWorkspaceRoot();
process.env.CHIPVERIFY_WORKSPACE_ROOT = PROJECT_ROOT;

console.info("[Electron] Workspace root resolved:", process.env.CHIPVERIFY_WORKSPACE_ROOT);

function getBackendUrl() {
  return normalizeBackendBaseUrl(process.env.CHIPVERIFY_BACKEND_URL || "") || DEFAULT_BACKEND_URL;
}

function readPackagedConvexSiteUrl() {
  try {
    const pkg = require("../package.json");
    const configured = pkg?.chipverify?.convexSiteUrl;
    if (typeof configured === "string" && configured.trim()) {
      return configured.trim().replace(/\/+$/, "");
    }
  } catch {
    // Ignore missing package metadata in unusual launch contexts.
  }
  return "https://next-swordfish-62.convex.site";
}

function getConvexSiteUrl() {
  return String(
    process.env.CHIPVERIFY_CONVEX_SITE_URL
    || process.env.CONVEX_SITE_URL
    || readPackagedConvexSiteUrl(),
  ).replace(/\/+$/, "");
}

function boolFromEnv(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized) return null;
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  return null;
}

function shouldRequireActivation() {
  const override = boolFromEnv(process.env.CHIPVERIFY_REQUIRE_ACTIVATION);
  if (override !== null) {
    return override;
  }
  return app.isPackaged;
}

function getActivationFilePath() {
  return path.join(app.getPath("userData"), "activation.json");
}

function getSecureSecretsPath() {
  return path.join(app.getPath("userData"), "secrets.json");
}

function isSecureKeyStorageAvailable() {
  try {
    return Boolean(safeStorage?.isEncryptionAvailable?.());
  } catch {
    return false;
  }
}

function readSecureSecrets() {
  const secretsPath = getSecureSecretsPath();
  try {
    if (!fs.existsSync(secretsPath)) {
      return {};
    }
    const parsed = JSON.parse(fs.readFileSync(secretsPath, "utf8"));
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (error) {
    console.warn("[Electron] Failed to read secure secrets metadata", { message: error?.message });
    return {};
  }
}

function writeSecureSecrets(nextSecrets) {
  const secretsPath = getSecureSecretsPath();
  fs.mkdirSync(path.dirname(secretsPath), { recursive: true });
  fs.writeFileSync(secretsPath, `${JSON.stringify(nextSecrets, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
}

function saveOpenAiApiKeySecurely(rawApiKey) {
  const apiKey = String(rawApiKey || "").trim();
  if (!apiKey) {
    return false;
  }
  if (!isSecureKeyStorageAvailable()) {
    throw new Error("Secure local key storage is unavailable on this machine.");
  }

  const encrypted = safeStorage.encryptString(apiKey).toString("base64");
  const existing = readSecureSecrets();
  writeSecureSecrets({
    ...existing,
    version: 1,
    openai: {
      encryptedApiKey: encrypted,
      provider: "openai",
      updatedAt: new Date().toISOString(),
      storage: process.platform === "win32" ? "windows-dpapi" : "electron-safeStorage",
    },
  });
  return true;
}

function readOpenAiApiKeySecurely() {
  if (!isSecureKeyStorageAvailable()) {
    return "";
  }
  const secrets = readSecureSecrets();
  const encrypted = secrets?.openai?.encryptedApiKey;
  if (!encrypted || typeof encrypted !== "string") {
    return "";
  }
  try {
    return safeStorage.decryptString(Buffer.from(encrypted, "base64")).trim();
  } catch (error) {
    console.warn("[Electron] Failed to decrypt stored OpenAI API key", { message: error?.message });
    return "";
  }
}

function hasStoredOpenAiApiKey() {
  const secrets = readSecureSecrets();
  return Boolean(secrets?.openai?.encryptedApiKey);
}

function applySecureLlmSecretsToEnv() {
  const provider = String(process.env.CHIPVERIFY_LLM_PROVIDER || process.env.MODEL_PROVIDER || "")
    .trim()
    .toLowerCase()
    .replace(/-/g, "_");
  const shouldApplyOpenAi =
    provider === "openai"
    || provider === "openai_sdk"
    || provider === "chatgpt"
    || (!provider && hasStoredOpenAiApiKey());

  if (!shouldApplyOpenAi) {
    return;
  }

  const apiKey = readOpenAiApiKeySecurely();
  if (!apiKey) {
    return;
  }

  process.env.CHIPVERIFY_LLM_PROVIDER = "openai";
  process.env.MODEL_PROVIDER = "openai";
  process.env.OPENAI_API_KEY = process.env.OPENAI_API_KEY || apiKey;
  process.env.CHIPVERIFY_OPENAI_API_KEY = process.env.CHIPVERIFY_OPENAI_API_KEY || apiKey;
  process.env.CHIPVERIFY_LLM_API_KEY = process.env.CHIPVERIFY_LLM_API_KEY || apiKey;
  process.env.CHIPVERIFY_LLM_API_KEY_REQUIRED = process.env.CHIPVERIFY_LLM_API_KEY_REQUIRED || "false";
}

function toSqliteUrl(filePath) {
  return `sqlite:///${String(filePath || "").replace(/\\/g, "/")}`;
}

function getRuntimeEnvSearchPaths() {
  return [
    path.join(PROJECT_ROOT, ".env"),
    path.join(PROJECT_ROOT, "backend", ".env"),
    path.join(APP_CONTENT_ROOT, ".env"),
    path.join(APP_CONTENT_ROOT, "backend", ".env"),
    path.join(RESOURCE_ROOT, "runtime", "backend", ".env"),
    path.join(RESOURCE_ROOT, "backend", ".env"),
  ];
}

function resolvePackagedLicensePath() {
  const candidates = [
    path.join(RESOURCE_ROOT, "runtime", "backend", "license.key"),
    path.join(RESOURCE_ROOT, "backend", "license.key"),
    path.join(APP_CONTENT_ROOT, "backend", "license.key"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || "";
}

function applyDesktopActivationEnv() {
  const status = getActivationStatus(getActivationFilePath(), {
    requiresActivation: shouldRequireActivation(),
  });

  if (!status.activated) {
    return status;
  }

  process.env.CHIPVERIFY_DESKTOP_MACHINE_ID = status.machineId || getMachineId();
  process.env.CHIPVERIFY_DESKTOP_ACTIVATION_HASH = status.activationHash || "";
  process.env.CHIPVERIFY_DESKTOP_ACTIVATED = "1";
  process.env.CHIPVERIFY_LICENSE_SECRET = process.env.CHIPVERIFY_LICENSE_SECRET || getLicenseSecret();
  process.env.CHIPVERIFY_DESKTOP_ACTIVATION_BYPASS_LICENSE = "true";
  return status;
}

async function fetchJsonWithTimeout(url, options = {}, timeoutMs = 8000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });
    const text = await response.text();
    let body = {};
    try {
      body = text ? JSON.parse(text) : {};
    } catch {
      body = { raw: text };
    }
    if (!response.ok) {
      throw new Error(body?.error || body?.message || `Convex request failed (${response.status})`);
    }
    return body;
  } finally {
    clearTimeout(timer);
  }
}

function shouldUseCloudControl() {
  if (boolFromEnv(process.env.CHIPVERIFY_CLOUD_CONTROL_DISABLED) === true) {
    return false;
  }
  const enabled = boolFromEnv(process.env.CHIPVERIFY_CLOUD_CONTROL_ENABLED);
  if (enabled !== null) {
    return enabled;
  }
  return app.isPackaged;
}

function isCloudControlRequired() {
  const override = boolFromEnv(process.env.CHIPVERIFY_CLOUD_CONTROL_REQUIRED);
  if (override !== null) {
    return override;
  }
  return app.isPackaged;
}

function applyCloudRuntimeConfig(entitlement) {
  const runtimeConfig = entitlement?.runtimeConfig || {};
  if (!entitlement?.allowed || runtimeConfig.gatewayEnabled === false) {
    return;
  }

  const model = String(runtimeConfig.model || DEFAULT_OPENAI_MODEL).trim() || DEFAULT_OPENAI_MODEL;
  process.env.CHIPVERIFY_LLM_PROVIDER = "convex_gateway";
  process.env.MODEL_PROVIDER = "convex_gateway";
  process.env.MODEL_NAME = model;
  process.env.CHIPVERIFY_LLM_MODEL_ALIAS = model;
  process.env.CHIPVERIFY_CONVEX_MODEL = model;
  process.env.MODEL_MAX_TOKENS = String(runtimeConfig.maxTokens || process.env.MODEL_MAX_TOKENS || "16384");
  process.env.CHIPVERIFY_LLM_MAX_TOKENS = process.env.MODEL_MAX_TOKENS;
  process.env.MODEL_TEMPERATURE = String(runtimeConfig.temperature ?? process.env.MODEL_TEMPERATURE ?? "0.3");
  process.env.CHIPVERIFY_LLM_API_KEY_REQUIRED = "false";
  process.env.CHIPVERIFY_CONVEX_SITE_URL = getConvexSiteUrl();
  process.env.CHIPVERIFY_CONVEX_LLM_GATEWAY_URL =
    process.env.CHIPVERIFY_CONVEX_LLM_GATEWAY_URL
    || `${getConvexSiteUrl()}/api/llm/generate`;
  process.env.CHIPVERIFY_CLOUD_ALLOWED = "1";
  process.env.CHIPVERIFY_CLOUD_LICENSE_STATUS = entitlement.status || "active";
}

async function applyCloudControlEnv(activationStatus) {
  if (!shouldUseCloudControl()) {
    cloudEntitlementState = { skipped: true, reason: "disabled" };
    return cloudEntitlementState;
  }
  if (!activationStatus?.activated) {
    cloudEntitlementState = { skipped: true, reason: "not_activated" };
    return cloudEntitlementState;
  }

  const convexSiteUrl = getConvexSiteUrl();
  const query = new URLSearchParams({
    machineId: activationStatus.machineId || getMachineId(),
    platform: process.platform,
    appVersion: app.getVersion(),
    activationHash: activationStatus.activationHash || "",
  });
  const endpoint = `${convexSiteUrl}/api/desktop/entitlement?${query.toString()}`;

  try {
    const entitlement = await fetchJsonWithTimeout(endpoint, {}, Number(process.env.CHIPVERIFY_CLOUD_TIMEOUT_MS || 8000));
    cloudEntitlementState = {
      ...entitlement,
      convexSiteUrl,
      checkedAt: new Date().toISOString(),
    };
    if (entitlement?.allowed) {
      applyCloudRuntimeConfig(entitlement);
      void fetchJsonWithTimeout(
        `${convexSiteUrl}/api/desktop/heartbeat`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            machineId: activationStatus.machineId || getMachineId(),
            platform: process.platform,
            appVersion: app.getVersion(),
            activationHash: activationStatus.activationHash || "",
          }),
        },
        5000,
      ).catch((error) => {
        console.warn("[Electron] Convex heartbeat failed", { message: error?.message });
      });
      return cloudEntitlementState;
    }

    process.env.CHIPVERIFY_CLOUD_ALLOWED = "0";
    process.env.CHIPVERIFY_CLOUD_LICENSE_STATUS = entitlement?.status || "denied";
    return { ...cloudEntitlementState, denied: true };
  } catch (error) {
    cloudEntitlementState = {
      allowed: false,
      error: error?.message || "Convex entitlement check failed.",
      convexSiteUrl,
      checkedAt: new Date().toISOString(),
    };
    console.warn("[Electron] Convex entitlement check failed", cloudEntitlementState);
    if (isCloudControlRequired()) {
      return { ...cloudEntitlementState, denied: true };
    }
    return cloudEntitlementState;
  }
}

function ensureRuntimeEnvLoaded() {
  if (runtimeEnvLoaded) {
    return;
  }

  for (const envPath of getRuntimeEnvSearchPaths()) {
    loadEnvFile(envPath);
  }

  applyPackagedBedrockDefaults();
  applyDemoGeminiDefaults();
  applyPackagedOpenAiDefaults();
  applySecureLlmSecretsToEnv();
  normalizeModelProviderEnvAliases();

  const userDataPath = app.getPath("userData");
  fs.mkdirSync(PROJECT_ROOT, { recursive: true });
  fs.mkdirSync(path.join(userDataPath, "outputs"), { recursive: true });
  fs.mkdirSync(path.join(userDataPath, "runtime"), { recursive: true });

  process.env.CHIPVERIFY_SECRET_KEY =
    process.env.CHIPVERIFY_SECRET_KEY
    || crypto
      .createHash("sha256")
      .update(`${getMachineId()}:${getLicenseSecret()}:chipverify-local-auth`)
      .digest("hex");
  process.env.DATABASE_URL =
    process.env.DATABASE_URL || toSqliteUrl(path.join(userDataPath, "chipverify.db"));
  process.env.CHIPVERIFY_OUTPUTS_DIR =
    process.env.CHIPVERIFY_OUTPUTS_DIR || path.join(userDataPath, "outputs");
  process.env.CHIPVERIFY_ALLOW_DEV_AUTH =
    process.env.CHIPVERIFY_ALLOW_DEV_AUTH || "true";

  const packagedLicensePath = resolvePackagedLicensePath();
  if (packagedLicensePath && !process.env.CHIPVERIFY_LICENSE_PATH) {
    process.env.CHIPVERIFY_LICENSE_PATH = packagedLicensePath;
  }

  applyDesktopActivationEnv();
  runtimeEnvLoaded = true;
}

const FRONTEND_DEV_URL = process.env.CHIPVERIFY_FRONTEND_DEV_URL || "http://localhost:5173";
const PRELOAD_PATH = path.join(__dirname, "preload.js");

function resolveManagedBackendExecutable() {
  const executableName = process.platform === "win32" ? "chipverify-backend.exe" : "chipverify-backend";
  const candidates = [
    path.join(RESOURCE_ROOT, "runtime", "backend", executableName),
    path.join(RESOURCE_ROOT, "backend", executableName),
  ];
  const elfPath = candidates.find((candidate) => fs.existsSync(candidate)) || "";

  // On Linux, prefer the self-diagnosing launch wrapper when it is present next to
  // the ELF. It records ldd/glibc diagnostics into the backend log before exec'ing
  // the real binary, which is what surfaces the root cause when the frozen backend
  // fails to load on RHEL/Alma/Rocky (glibc/libstdc++ mismatch).
  if (elfPath && process.platform === "linux") {
    const wrapperPath = path.join(path.dirname(elfPath), "launch-backend.sh");
    if (fs.existsSync(wrapperPath)) {
      return wrapperPath;
    }
  }

  return elfPath;
}

function getManagedBackendLogPath() {
  return path.join(app.getPath("userData"), "logs", "backend.log");
}

function resolveManagedBackendBundledEnvPath(executablePath) {
  if (!executablePath) {
    return "";
  }
  const envPath = path.join(path.dirname(executablePath), ".env");
  return fs.existsSync(envPath) ? envPath : "";
}

function buildManagedBackendLibraryPath(executablePath) {
  if (process.platform === "win32" || !executablePath) {
    return process.env.LD_LIBRARY_PATH || "";
  }

  const backendDir = path.dirname(executablePath);
  const candidates = [
    path.join(backendDir, "_internal"),
    backendDir,
  ];
  const existing = (process.env.LD_LIBRARY_PATH || "")
    .split(":")
    .filter(Boolean);
  const merged = [...candidates, ...existing.filter((entry) => !candidates.includes(entry))];
  return merged.join(":");
}

function resolvePackagedRuntimeTool(toolName) {
  const executableName = process.platform === "win32" ? `${toolName}.exe` : toolName;
  const candidates = [
    path.join(RESOURCE_ROOT, "runtime", "bin", executableName),
    path.join(RESOURCE_ROOT, "bin", executableName),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || "";
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForBackendHealth(timeoutMs = 7000) {
  const deadline = Date.now() + timeoutMs;
  const backendUrl = getBackendUrl();
  const urls = [
    `${backendUrl}/api/v1/health`,
    `${backendUrl}/health`,
  ];

  while (Date.now() < deadline) {
    for (const url of urls) {
      try {
        const response = await fetch(url);
        if (response.ok) {
          return true;
        }
      } catch {
        // Backend is still starting.
      }
    }
    await sleep(400);
  }

  return false;
}

function resolveCommandPath(commandName) {
  if (!commandName) {
    return "";
  }
  if (path.isAbsolute(commandName) && fs.existsSync(commandName)) {
    return commandName;
  }

  const lookup = process.platform === "win32" ? "where" : "which";
  const result = spawnSync(lookup, [commandName], {
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.status !== 0 || !result.stdout) {
    return "";
  }
  return result.stdout.split(/\r?\n/).map((line) => line.trim()).find(Boolean) || "";
}

function resolveBackendSourceRoot() {
  const candidates = [
    process.env.CHIPVERIFY_BACKEND_SOURCE_DIR,
    path.join(RESOURCE_ROOT, "runtime", "backend_source"),
    app.isPackaged ? path.resolve(path.dirname(process.execPath), "..", "..", "backend") : "",
    path.join(resolveRepoRoot(), "backend"),
  ].filter(Boolean);

  for (const candidate of candidates) {
    const normalized = path.resolve(candidate);
    if (fs.existsSync(path.join(normalized, "main.py"))) {
      return normalized;
    }
  }

  return "";
}

function resolvePythonBackendFallback() {
  if (app.isPackaged && process.env.CHIPVERIFY_ENABLE_PACKAGED_SOURCE_FALLBACK !== "true") {
    return null;
  }

  const backendSourceRoot = resolveBackendSourceRoot();
  if (!backendSourceRoot) {
    return null;
  }

  const pythonCandidates = [
    process.env.CHIPVERIFY_PYTHON_BACKEND_BIN,
    process.env.CHIPIX_PYTHON,
    process.env.PYTHON,
    "python",
  ].filter(Boolean);

  for (const candidate of pythonCandidates) {
    const pythonPath = resolveCommandPath(candidate);
    if (pythonPath) {
      return {
        executablePath: pythonPath,
        args: [
          "-m",
          "uvicorn",
          "main:app",
          "--host",
          process.env.CHIPVERIFY_BACKEND_HOST || "127.0.0.1",
          "--port",
          process.env.CHIPVERIFY_BACKEND_PORT || "7348",
        ],
        cwd: backendSourceRoot,
        label: "python-source-fallback",
      };
    }
  }

  return null;
}

function appendBackendLog(backendLogPath, streamName, chunk) {
  try {
    fs.appendFileSync(
      backendLogPath,
      `[${new Date().toISOString()}] [${streamName}] ${Buffer.from(String(chunk)).toString("utf8")}`,
      "utf8",
    );
  } catch {
    // Best-effort diagnostics only.
  }
}

function readBackendLogTail(backendLogPath, maxBytes = 12000) {
  try {
    if (!backendLogPath || !fs.existsSync(backendLogPath)) {
      return "";
    }
    const stats = fs.statSync(backendLogPath);
    const bytesToRead = Math.min(maxBytes, stats.size);
    const fd = fs.openSync(backendLogPath, "r");
    try {
      const buffer = Buffer.alloc(bytesToRead);
      fs.readSync(fd, buffer, 0, bytesToRead, Math.max(0, stats.size - bytesToRead));
      return buffer.toString("utf8");
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return "";
  }
}

function spawnManagedBackendProcess(launch, backendLogPath) {
  const packagedSlang = resolvePackagedRuntimeTool("slang");
  const packagedSvls = resolvePackagedRuntimeTool("svls");
  const bundledEnvPath = resolveManagedBackendBundledEnvPath(launch.executablePath);
  const backendEnv = {
    ...process.env,
    CHIPVERIFY_BACKEND_HOST: process.env.CHIPVERIFY_BACKEND_HOST || "127.0.0.1",
    CHIPVERIFY_BACKEND_PORT: process.env.CHIPVERIFY_BACKEND_PORT || "7348",
    CHIPVERIFY_WORKSPACE_ROOT: PROJECT_ROOT,
    CHIPVERIFY_RESOURCES_DIR: RESOURCE_ROOT,
    CHIPVERIFY_BACKEND_LOG: backendLogPath,
    CHIPVERIFY_SLANG_BIN: process.env.CHIPVERIFY_SLANG_BIN || packagedSlang,
    CHIPVERIFY_SVLS_BIN: process.env.CHIPVERIFY_SVLS_BIN || packagedSvls,
  };

  if (bundledEnvPath) {
    backendEnv.CHIPVERIFY_ENV_FILE = bundledEnvPath;
  }
  if (process.platform !== "win32") {
    backendEnv.LD_LIBRARY_PATH = buildManagedBackendLibraryPath(launch.executablePath);
  }

  managedBackendDiagnostics = {
    executablePath: launch.executablePath,
    cwd: launch.cwd,
    backendUrl: getBackendUrl(),
    backendLogPath,
    label: launch.label,
    startedAt: new Date().toISOString(),
    exitedAt: "",
    exitCode: null,
    signal: null,
    error: "",
  };
  managedBackendProcess = spawn(launch.executablePath, launch.args || [], {
    cwd: launch.cwd,
    env: backendEnv,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });

  managedBackendProcess.stdout?.on("data", (chunk) => appendBackendLog(backendLogPath, "stdout", chunk));
  managedBackendProcess.stderr?.on("data", (chunk) => appendBackendLog(backendLogPath, "stderr", chunk));

  managedBackendProcess.on("error", (error) => {
    managedBackendDiagnostics.error = error?.message || String(error);
    managedBackendDiagnostics.exitedAt = new Date().toISOString();
    console.error("[Electron] Managed backend failed to start", {
      executablePath: launch.executablePath,
      label: launch.label,
      message: error?.message,
    });
    appendBackendLog(backendLogPath, "error", `${error?.stack || error?.message || error}\n`);
    captureElectronException(error, {
      area: "managed_backend_start",
      executablePath: launch.executablePath,
      label: launch.label,
    });
    managedBackendProcess = null;
  });

  managedBackendProcess.on("exit", (code, signal) => {
    managedBackendDiagnostics.exitCode = code;
    managedBackendDiagnostics.signal = signal;
    managedBackendDiagnostics.exitedAt = new Date().toISOString();
    console.warn("[Electron] Managed backend exited", { code, signal, label: launch.label });
    appendBackendLog(backendLogPath, "exit", `Managed backend exited label=${launch.label} code=${code} signal=${signal}\n`);
    if (code !== 0 && code !== null) {
      captureElectronMessage("Managed backend exited unexpectedly", "error", {
        area: "managed_backend_exit",
        exitCode: code,
        signal: signal || null,
        executablePath: launch.executablePath,
        backendLogTail: readLogTail(backendLogPath),
      });
    }
    managedBackendProcess = null;
  });

  managedBackendProcess.unref?.();
  console.info("[Electron] Managed backend started", {
    executablePath: launch.executablePath,
    label: launch.label,
    backendUrl: getBackendUrl(),
  });
}

function appendLinuxBackendDiagnostics(logPath, executablePath) {
  if (process.platform !== "linux") {
    return;
  }

  try {
    const elfPath = path.join(path.dirname(executablePath || ""), "chipverify-backend");
    const script =
      'echo "=== electron backend diagnostics ===";' +
      "ldd --version 2>&1 | head -1;" +
      'if [ -x "$0" ]; then echo "ldd $0:"; ldd "$0" 2>&1 | grep -i "not found" || echo "  all libs resolved"; ' +
      'else echo "backend ELF missing: $0"; fi';
    const diagnostic = spawnSync("bash", ["-c", script, elfPath], {
      encoding: "utf8",
      timeout: 10000,
    });
    const output = `${diagnostic.stdout || ""}${diagnostic.stderr || ""}`.trim();
    if (output) {
      fs.appendFileSync(logPath, `\n${output}\n`);
    }
  } catch {
    // Diagnostics must not block startup or shutdown.
  }
}

async function startManagedBackendIfAvailable() {
  if (managedBackendProcess) {
    return { started: true, alreadyRunning: true, executablePath: managedBackendProcess.spawnfile || null };
  }

  const executablePath = resolveManagedBackendExecutable();
  const fallbackLaunch = resolvePythonBackendFallback();
  if (!executablePath && !fallbackLaunch) {
    return { started: false, reason: "MANAGED_BACKEND_NOT_FOUND" };
  }

  ensureRuntimeEnvLoaded();
  const activationStatus = applyDesktopActivationEnv();
  const cloudState = await applyCloudControlEnv(activationStatus);
  if (cloudState?.denied) {
    // Cloud control gates paid/remote LLM capability, but it must not prevent
    // the local FastAPI backend from starting. Project creation, file upload,
    // activation UI, and diagnostics all depend on the local backend even when
    // Convex is temporarily unreachable or an entitlement is revoked.
    console.warn("[Electron] Cloud entitlement denied or unavailable; starting local backend anyway", {
      status: cloudState?.status,
      reason: cloudState?.reason,
      error: cloudState?.error,
    });
  }

  if (await waitForBackendHealth(1200)) {
    return {
      started: true,
      alreadyRunning: true,
      executablePath: null,
      reason: "BACKEND_ALREADY_HEALTHY",
    };
  }

  const cwd = path.join(app.getPath("userData"), "runtime");
  fs.mkdirSync(cwd, { recursive: true });

  const backendLogPath = getManagedBackendLogPath();
  try {
    fs.mkdirSync(path.dirname(backendLogPath), { recursive: true });
    fs.appendFileSync(
      backendLogPath,
      `\n[${new Date().toISOString()}] Starting managed backend: ${executablePath || fallbackLaunch?.executablePath}\n`,
      "utf8",
    );
  } catch {
    // Logging must never block app startup.
  }

  if (executablePath) {
    spawnManagedBackendProcess({ executablePath, args: [], cwd, label: "pyinstaller" }, backendLogPath);
    const startupTimeoutMs = Number(process.env.CHIPVERIFY_BACKEND_STARTUP_TIMEOUT_MS || (app.isPackaged ? 45000 : 15000));
    if (await waitForBackendHealth(startupTimeoutMs)) {
      return { started: true, alreadyRunning: false, executablePath };
    }
    appendBackendLog(
      backendLogPath,
      "warning",
      `PyInstaller backend did not become healthy within ${startupTimeoutMs}ms.\n`,
    );
    appendLinuxBackendDiagnostics(backendLogPath, executablePath);
    captureElectronMessage("Managed backend failed health check", "error", {
      area: "managed_backend_health",
      backendUrl: getBackendUrl(),
      executablePath,
      backendLogTail: readLogTail(backendLogPath),
    });
    if (app.isPackaged) {
      appendBackendLog(backendLogPath, "warning", "Keeping packaged backend process alive; source fallback is disabled in packaged builds.\n");
      return { started: false, alreadyRunning: false, executablePath, reason: "MANAGED_BACKEND_UNHEALTHY" };
    }
    try {
      managedBackendProcess?.kill();
    } catch {
      // Ignore cleanup races.
    }
    managedBackendProcess = null;
  }

  if (fallbackLaunch) {
    appendBackendLog(
      backendLogPath,
      "fallback",
      `Starting Python backend fallback: ${fallbackLaunch.executablePath} cwd=${fallbackLaunch.cwd}\n`,
    );
    spawnManagedBackendProcess(fallbackLaunch, backendLogPath);
    const healthy = await waitForBackendHealth(10000);
    return {
      started: healthy,
      alreadyRunning: false,
      executablePath: fallbackLaunch.executablePath,
      fallback: true,
      reason: healthy ? undefined : "PYTHON_BACKEND_FALLBACK_UNHEALTHY",
    };
  }

  return { started: false, reason: "MANAGED_BACKEND_UNHEALTHY" };
}

function createWindow() {
  // Fully frameless on Windows/Linux (we draw our own controls in the React
  // title bar). On macOS, `hiddenInset` keeps the traffic-light buttons in
  // their native position so users get the OS pattern they expect.
  const isMac = process.platform === "darwin";
  mainWindow = new BrowserWindow({
    width: 1480,
    height: 920,
    minWidth: 1180,
    minHeight: 720,
    show: false,
    titleBarStyle: isMac ? "hiddenInset" : "hidden",
    trafficLightPosition: isMac ? { x: 14, y: 12 } : undefined,
    webPreferences: {
      preload: PRELOAD_PATH,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: true
    },
  });

  // Window control IPC — invoked by the React title bar.
  ipcMain.handle("chipverify:window:minimize", () => {
    mainWindow?.minimize();
    return true;
  });
  ipcMain.handle("chipverify:window:toggle-maximize", () => {
    if (!mainWindow) return false;
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize();
      return false;
    }
    mainWindow.maximize();
    return true;
  });
  ipcMain.handle("chipverify:window:close", () => {
    mainWindow?.close();
    return true;
  });
  ipcMain.handle("chipverify:window:is-maximized", () => Boolean(mainWindow?.isMaximized()));

  // Stream maximize-state changes so the React title bar can swap the
  // restore/maximize glyph in real time.
  const notifyMaximizeState = () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    mainWindow.webContents.send(
      "chipverify:window:maximize-state",
      mainWindow.isMaximized(),
    );
  };
  mainWindow.on("maximize", notifyMaximizeState);
  mainWindow.on("unmaximize", notifyMaximizeState);

  mainWindow.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    console.error("[Electron] Renderer did-fail-load", {
      errorCode,
      errorDescription,
      validatedURL,
      isMainFrame,
    });
    // A failed main-frame load is what a user sees as a blank/white window.
    if (isMainFrame) {
      captureElectronMessage("Renderer failed to load", "error", {
        area: "renderer_did_fail_load",
        errorCode,
        errorDescription,
        validatedURL,
      });
    }
  });

  mainWindow.webContents.on("render-process-gone", (_event, details) => {
    console.error("[Electron] Renderer render-process-gone", details);
    // Native renderer crash (the renderer SDK can't report its own death — main must).
    captureElectronMessage("Renderer process gone", "fatal", {
      area: "render_process_gone",
      reason: details?.reason,
      exitCode: details?.exitCode,
    });
  });

  mainWindow.webContents.on("unresponsive", () => {
    console.error("[Electron] Renderer unresponsive");
    captureElectronMessage("Renderer became unresponsive", "warning", {
      area: "renderer_unresponsive",
    });
  });

  // Basic native context menu
  mainWindow.webContents.on("context-menu", (event, params) => {
    const { Menu, MenuItem } = require("electron");
    const menu = new Menu();

    // Add spelling suggestions if applicable
    if (params.dictionarySuggestions && params.dictionarySuggestions.length > 0) {
      for (const suggestion of params.dictionarySuggestions) {
        menu.append(new MenuItem({ label: suggestion, click: () => mainWindow.webContents.replaceMisspelling(suggestion) }));
      }
      menu.append(new MenuItem({ type: "separator" }));
    }

    if (params.linkURL) {
      menu.append(new MenuItem({ label: "Copy Link Address", role: "copy" }));
    }

    if (params.hasImageContents) {
      menu.append(new MenuItem({ label: "Copy Image", role: "copyImage" }));
    }

    if (params.isEditable) {
      menu.append(new MenuItem({ label: "Undo", role: "undo" }));
      menu.append(new MenuItem({ label: "Redo", role: "redo" }));
      menu.append(new MenuItem({ type: "separator" }));
      menu.append(new MenuItem({ label: "Cut", role: "cut" }));
      menu.append(new MenuItem({ label: "Copy", role: "copy" }));
      menu.append(new MenuItem({ label: "Paste", role: "paste" }));
      menu.append(new MenuItem({ type: "separator" }));
      menu.append(new MenuItem({ label: "Select All", role: "selectAll" }));
    } else if (params.selectionText) {
      menu.append(new MenuItem({ label: "Copy", role: "copy" }));
    }

    // Tools for debugging
    menu.append(new MenuItem({ type: "separator" }));
    menu.append(
      new MenuItem({
        label: "Inspect Element",
        click: () => mainWindow.webContents.inspectElement(params.x, params.y),
      })
    );

    menu.popup({ window: mainWindow, x: params.x, y: params.y });
  });

  const isDev = !app.isPackaged;
  if (isDev) {
    mainWindow.loadURL(FRONTEND_DEV_URL);
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "frontend", "dist", "index.html"));
  }

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    app.emit("chipverify:main-window-ready");
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function createApplicationMenu() {
  const isMac = process.platform === "darwin";
  const helpSubmenu = [
    {
      label: "Check for Updates…",
      click: () => scheduleAutoUpdateCheck(() => mainWindow),
    },
    { type: "separator" },
    {
      label: "Learn More",
      click: () => shell.openExternal("https://chipverify.com"),
    },
  ];

  if (!isMac) {
    const template = [
      {
        label: "Help",
        submenu: helpSubmenu,
      },
    ];
    Menu.setApplicationMenu(Menu.buildFromTemplate(template));
    return;
  }

  const template = [
    {
      label: app.name,
      submenu: [
        { role: "about" },
        { type: "separator" },
        { role: "services" },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    {
      label: "File",
      submenu: [{ role: "close" }],
    },
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "pasteAndMatchStyle" },
        { role: "delete" },
        { role: "selectAll" },
        { type: "separator" },
        {
          label: "Speech",
          submenu: [{ role: "startSpeaking" }, { role: "stopSpeaking" }],
        },
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    {
      label: "Window",
      submenu: [
        { role: "minimize" },
        { role: "zoom" },
        { type: "separator" },
        { role: "front" },
        { type: "separator" },
        { role: "window" },
      ],
    },
    {
      label: "Help",
      submenu: helpSubmenu,
    },
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

ipcMain.handle("chipverify:get-backend-url", async () => getBackendUrl());
ipcMain.handle("chipverify:backend:diagnostics", async () => {
  const executablePath = resolveManagedBackendExecutable();
  const backendLogPath = managedBackendDiagnostics.backendLogPath
    || path.join(app.getPath("userData"), "logs", "backend.log");
  return {
    ...managedBackendDiagnostics,
    executablePath: managedBackendDiagnostics.executablePath || executablePath,
    executableExists: Boolean(executablePath && fs.existsSync(executablePath)),
    backendUrl: getBackendUrl(),
    resourceRoot: RESOURCE_ROOT,
    appData: app.getPath("userData"),
    platform: process.platform,
    arch: process.arch,
    packaged: app.isPackaged,
    backendLogPath,
    logTail: readBackendLogTail(backendLogPath),
  };
});
ipcMain.handle("chipverify:get-workspace-root", async () => process.env.CHIPVERIFY_WORKSPACE_ROOT);
ipcMain.handle("chipverify:activation:status", async () => {
  const status = getActivationStatus(getActivationFilePath(), {
    requiresActivation: shouldRequireActivation(),
  });

  if (status.activated) {
    ensureRuntimeEnvLoaded();
    await startManagedBackendIfAvailable();
  }

  return {
    ...status,
    backendUrl: getBackendUrl(),
    managedBackendAvailable: Boolean(resolveManagedBackendExecutable()),
    secureApiKeyStorageAvailable: isSecureKeyStorageAvailable(),
    secureOpenAiKeyPresent: hasStoredOpenAiApiKey(),
    cloudEntitlement: cloudEntitlementState,
    packaged: app.isPackaged,
  };
});

ipcMain.handle("chipverify:activation:activate", async (_event, payload = {}) => {
  const result = activateMachine(getActivationFilePath(), payload.machineKey || payload.key || "", {
    requiresActivation: shouldRequireActivation(),
  });
  let secureApiKeySaved = false;

  if (result.activated) {
    const openAiApiKey = String(payload.openAiApiKey || payload.openaiApiKey || "").trim();
    if (openAiApiKey) {
      try {
        secureApiKeySaved = saveOpenAiApiKeySecurely(openAiApiKey);
        applySecureLlmSecretsToEnv();
      } catch (error) {
        try {
          fs.unlinkSync(getActivationFilePath());
        } catch {
          // Activation rollback best effort only.
        }
        return {
          ...getActivationStatus(getActivationFilePath(), {
            requiresActivation: shouldRequireActivation(),
          }),
          backendUrl: getBackendUrl(),
          managedBackendAvailable: Boolean(resolveManagedBackendExecutable()),
          secureApiKeyStorageAvailable: isSecureKeyStorageAvailable(),
          secureOpenAiKeyPresent: hasStoredOpenAiApiKey(),
          packaged: app.isPackaged,
          error: "SECURE_API_KEY_SAVE_FAILED",
          message: error?.message || "Unable to save the OpenAI API key securely.",
        };
      }
    }
    ensureRuntimeEnvLoaded();
    await startManagedBackendIfAvailable();
  }

  return {
    ...result,
    backendUrl: getBackendUrl(),
    managedBackendAvailable: Boolean(resolveManagedBackendExecutable()),
    secureApiKeyStorageAvailable: isSecureKeyStorageAvailable(),
    secureOpenAiKeyPresent: hasStoredOpenAiApiKey(),
    secureApiKeySaved,
    cloudEntitlement: cloudEntitlementState,
    packaged: app.isPackaged,
  };
});

const RTL_SOURCE_EXTENSIONS = new Set([".v", ".sv", ".svh", ".vh", ".vhd", ".vhdl"]);
const MAX_RTL_PICK_FILES = 5000;
const MAX_RTL_PICK_BYTES = 512 * 1024 * 1024;
const SKIPPED_RTL_DIRS = new Set([".git", "node_modules", "outputs", "dist", "build", "__pycache__"]);

function toPickedFilePayload(filePath, relativePath = "") {
  const stats = fs.statSync(filePath);
  const buffer = fs.readFileSync(filePath);

  return {
    name: path.basename(filePath),
    path: filePath,
    relativePath: relativePath || path.basename(filePath),
    size: stats.size,
    type: "",
    lastModified: stats.mtimeMs,
    content: buffer.toString("base64"),
  };
}

function isRtlSourcePath(filePath) {
  return RTL_SOURCE_EXTENSIONS.has(path.extname(filePath).toLowerCase());
}

function collectRtlFilesFromDirectory(rootDir) {
  const files = [];
  let totalBytes = 0;

  function visit(dir) {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const absPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (!SKIPPED_RTL_DIRS.has(entry.name)) {
          visit(absPath);
        }
        continue;
      }
      if (!entry.isFile() || !isRtlSourcePath(absPath)) {
        continue;
      }

      const stats = fs.statSync(absPath);
      totalBytes += stats.size;
      if (files.length >= MAX_RTL_PICK_FILES) {
        throw new Error(`RTL folder exceeds ${MAX_RTL_PICK_FILES} source files.`);
      }
      if (totalBytes > MAX_RTL_PICK_BYTES) {
        throw new Error(`RTL folder exceeds ${Math.floor(MAX_RTL_PICK_BYTES / (1024 * 1024))} MB.`);
      }

      files.push(toPickedFilePayload(absPath, path.relative(rootDir, absPath).replace(/\\/g, "/")));
    }
  }

  visit(rootDir);
  return files;
}

ipcMain.handle("chipverify:pick-file", async (event, options = {}) => {
  const { filters = [], title = "Open File" } = options;
  const result = await dialog.showOpenDialog(mainWindow, {
    title,
    properties: ["openFile"],
    filters,
  });

  if (result.canceled || result.filePaths.length === 0) {
    return null;
  }

  const filePath = result.filePaths[0];
  return toPickedFilePayload(filePath);
});

ipcMain.handle("chipverify:pick-files", async (event, options = {}) => {
  const { filters = [], title = "Open Files" } = options;
  const result = await dialog.showOpenDialog(mainWindow, {
    title,
    properties: ["openFile", "multiSelections"],
    filters,
  });

  if (result.canceled || result.filePaths.length === 0) {
    return [];
  }

  return result.filePaths.map((filePath) => toPickedFilePayload(filePath));
});

ipcMain.handle("chipverify:pick-directory", async (event, options = {}) => {
  const { title = "Open Folder" } = options;
  const result = await dialog.showOpenDialog(mainWindow, {
    title,
    properties: ["openDirectory"],
  });

  if (result.canceled || result.filePaths.length === 0) {
    return null;
  }

  const directoryPath = result.filePaths[0];
  return {
    name: path.basename(directoryPath),
    path: directoryPath,
    files: collectRtlFilesFromDirectory(directoryPath),
  };
});

// Sentry must be initialized BEFORE the app 'ready' event so it can install Chromium's
// native crash handlers. Load runtime env first (so DSN/environment from .env apply),
// then init Sentry — both at module load, before whenReady fires.
ensureRuntimeEnvLoaded();
initElectronSentry();

app.whenReady().then(async () => {
  console.info("[Electron] App ready: preparing Mastra IPC registration", {
    preloadPath: PRELOAD_PATH,
    isPackaged: app.isPackaged,
  });

  // Catch crashes of any Electron child process (GPU, utility, renderer, pepper)
  // so they surface in Sentry instead of vanishing silently.
  app.on("child-process-gone", (_event, details) => {
    console.error("[Electron] child-process-gone", details);
    captureElectronMessage("Electron child process gone", "error", {
      area: "child_process_gone",
      type: details?.type,
      reason: details?.reason,
      exitCode: details?.exitCode,
      serviceName: details?.serviceName,
      name: details?.name,
    });
  });

  // Last-resort handlers for the main process. @sentry/electron also installs an
  // uncaughtException handler; these add explicit context and cover rejections.
  process.on("unhandledRejection", (reason) => {
    console.error("[Electron] unhandledRejection", reason);
    const error = reason instanceof Error ? reason : new Error(String(reason));
    captureElectronException(error, { area: "main_unhandled_rejection" });
  });

  const activationStatus = getActivationStatus(getActivationFilePath(), {
    requiresActivation: shouldRequireActivation(),
  });
  if (activationStatus.activated) {
    ensureRuntimeEnvLoaded();
    await startManagedBackendIfAvailable();
  }

  try {
    registerMastraCopilotIpc({
      ipcMain,
      getMainWindow: () => mainWindow,
    });
    console.info("[Electron] Mastra Copilot IPC registered successfully");
  } catch (error) {
    console.error("[Mastra IPC] Registration failed:", error);
    captureElectronException(error, { area: "mastra_ipc_registration" });
  }

  startAutoUpdateOnLaunch(() => mainWindow);

  createApplicationMenu();
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("before-quit", () => {
  if (managedBackendProcess) {
    try {
      if (process.platform === "win32") {
        managedBackendProcess.kill();
      } else {
        managedBackendProcess.kill("SIGTERM");
      }
    } catch {
      // Ignore shutdown races.
    }
    managedBackendProcess = null;
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
