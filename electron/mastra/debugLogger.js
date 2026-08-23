const fs = require("node:fs");
const path = require("node:path");

const DEFAULT_LOG_FILENAME = "mastra-copilot-debug.ndjson";
const MAX_STRING_LENGTH = 6000;

function isDebugEnabled() {
  const raw = String(process.env.CHIPVERIFY_MASTRA_DEBUG || "").trim().toLowerCase();
  if (!raw) {
    return process.env.NODE_ENV !== "production";
  }

  return !["0", "false", "off", "no"].includes(raw);
}

function clip(value, max = MAX_STRING_LENGTH) {
  const text = String(value || "");
  if (text.length <= max) {
    return text;
  }

  return `${text.slice(0, max)}... [truncated ${text.length - max} chars]`;
}

function shouldRedactKey(key) {
  const normalized = String(key || "").toLowerCase();
  return (
    normalized.includes("api_key")
    || normalized.includes("apikey")
    || normalized.includes("token")
    || normalized.includes("authorization")
    || normalized.includes("secret")
    || normalized.includes("password")
  );
}

function sanitize(value, currentDepth = 0) {
  if (currentDepth > 5) {
    return "[max_depth_reached]";
  }

  if (value == null) {
    return value;
  }

  if (typeof value === "string") {
    return clip(value);
  }

  if (typeof value === "number" || typeof value === "boolean") {
    return value;
  }

  if (Array.isArray(value)) {
    return value.slice(0, 50).map((entry) => sanitize(entry, currentDepth + 1));
  }

  if (typeof value === "object") {
    const out = {};
    for (const [key, item] of Object.entries(value)) {
      if (shouldRedactKey(key)) {
        out[key] = "[redacted]";
      } else {
        out[key] = sanitize(item, currentDepth + 1);
      }
    }
    return out;
  }

  return String(value);
}

function resolveLogFile(workspaceRoot) {
  const root = path.resolve(
    workspaceRoot
    || process.env.CHIPVERIFY_WORKSPACE_ROOT
    || process.cwd(),
  );

  const logDir = path.join(root, "backend", "logs");
  fs.mkdirSync(logDir, { recursive: true });
  return path.join(logDir, DEFAULT_LOG_FILENAME);
}

function writeLine(line, workspaceRoot) {
  const target = resolveLogFile(workspaceRoot);
  fs.appendFileSync(target, `${line}\n`, "utf8");
}

function logDebug(event, payload = {}, options = {}) {
  if (!isDebugEnabled()) {
    return;
  }

  const entry = {
    ts: new Date().toISOString(),
    event,
    requestId: options.requestId || null,
    runId: options.runId || null,
    component: options.component || "mastra",
    payload: sanitize(payload),
  };

  try {
    writeLine(JSON.stringify(entry), options.workspaceRoot);
  } catch (error) {
    console.warn("[Mastra Debug] Failed to write debug line", error?.message || error);
  }

  try {
    console.info(`[Mastra Debug] ${event}`, entry.payload);
  } catch {
    // Ignore console serialization failures.
  }
}

module.exports = {
  isDebugEnabled,
  logDebug,
};
