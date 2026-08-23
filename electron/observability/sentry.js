const { app } = require("electron");
const fs = require("node:fs");

let initialized = false;

// Public client DSN (safe to embed — it can only send events, not read them).
// Override at runtime with CHIPVERIFY_SENTRY_DSN or SENTRY_DSN.
const DEFAULT_SENTRY_DSN =
  "https://f70efb11415defc8fe101378ef8a97c7@o4511364292673536.ingest.us.sentry.io/4511364402839552";

function getDsn() {
  return (
    process.env.CHIPVERIFY_SENTRY_DSN
    || process.env.SENTRY_DSN
    || DEFAULT_SENTRY_DSN
  ).trim();
}

function getRelease() {
  return (
    process.env.CHIPVERIFY_SENTRY_RELEASE
    || process.env.SENTRY_RELEASE
    || `chipverify-desktop@${app.getVersion()}`
  ).trim();
}

function getEnvironment() {
  return (
    process.env.CHIPVERIFY_SENTRY_ENVIRONMENT
    || process.env.SENTRY_ENVIRONMENT
    || (app.isPackaged ? "production" : "development")
  ).trim();
}

function getSentryMain() {
  try {
    return require("@sentry/electron/main");
  } catch {
    return null;
  }
}

function initElectronSentry() {
  if (initialized) {
    return Boolean(getDsn());
  }

  const dsn = getDsn();
  if (!dsn) {
    return false;
  }

  const sentry = getSentryMain();
  if (!sentry?.init) {
    return false;
  }

  const tracesSampleRate = Number(process.env.CHIPVERIFY_SENTRY_TRACES_SAMPLE_RATE || "0.2");

  sentry.init({
    dsn,
    environment: getEnvironment(),
    release: getRelease(),
    tracesSampleRate: Number.isFinite(tracesSampleRate) ? tracesSampleRate : 0.2,
    sendDefaultPii: false,
    // Capture crashes/uncaught errors in the main process and native (Crashpad)
    // crashes of child processes; without this a hard crash leaves no event.
    enableUncaughtExceptionHandler: true,
    beforeSend(event) {
      const headers = event?.request?.headers;
      if (headers && typeof headers === "object") {
        for (const key of Object.keys(headers)) {
          if (key.toLowerCase() === "authorization" || key.toLowerCase() === "cookie") {
            headers[key] = "[Filtered]";
          }
        }
      }
      return event;
    },
  });

  sentry.setTag("platform", process.platform);
  sentry.setTag("packaged", String(app.isPackaged));
  sentry.setTag("runtime", "electron-main");

  initialized = true;
  return true;
}

function withScope(callback) {
  const sentry = getSentryMain();
  if (!getDsn() || !sentry) {
    return;
  }
  if (!initialized) {
    initElectronSentry();
  }
  if (typeof sentry.withScope === "function") {
    sentry.withScope(callback);
    return;
  }
  callback(sentry.getCurrentScope?.() || {});
}

function captureElectronException(error, context = {}) {
  if (!getDsn()) {
    return;
  }
  withScope((scope) => {
    Object.entries(context).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        scope.setExtra(key, value);
      }
    });
    const sentry = getSentryMain();
    sentry?.captureException?.(error);
  });
}

function captureElectronMessage(message, level = "warning", context = {}) {
  if (!getDsn()) {
    return;
  }
  withScope((scope) => {
    Object.entries(context).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        scope.setExtra(key, value);
      }
    });
    const sentry = getSentryMain();
    sentry?.captureMessage?.(message, level);
  });
}

function readLogTail(logPath, maxBytes = 12000) {
  if (!logPath || !fs.existsSync(logPath)) {
    return "";
  }

  try {
    const stats = fs.statSync(logPath);
    const start = Math.max(0, stats.size - maxBytes);
    const length = stats.size - start;
    const buffer = Buffer.alloc(length);
    const fd = fs.openSync(logPath, "r");
    try {
      fs.readSync(fd, buffer, 0, length, start);
    } finally {
      fs.closeSync(fd);
    }
    return buffer.toString("utf8");
  } catch {
    return "";
  }
}

module.exports = {
  initElectronSentry,
  captureElectronException,
  captureElectronMessage,
  readLogTail,
};
