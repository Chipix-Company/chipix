import * as Sentry from "@sentry/react";

let initialized = false;

// Public client DSN (safe to embed — it can only send events, not read them).
// Override per build with VITE_SENTRY_DSN.
const DEFAULT_SENTRY_DSN =
  "https://f70efb11415defc8fe101378ef8a97c7@o4511364292673536.ingest.us.sentry.io/4511364402839552";

function getDsn() {
  return import.meta.env.VITE_SENTRY_DSN?.trim() || DEFAULT_SENTRY_DSN;
}

export function initSentry() {
  if (initialized || typeof window === "undefined") return false;
  const dsn = getDsn();
  if (!dsn) return false;

  const environment =
    import.meta.env.VITE_SENTRY_ENVIRONMENT?.trim()
    || (import.meta.env.PROD ? "production" : "development");

  const tracesSampleRate = Number(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE ?? "0.2");
  const isElectron = typeof window !== "undefined" && Boolean(window.electronAPI);

  const integrations = [
    Sentry.browserTracingIntegration(),
    Sentry.replayIntegration({
      maskAllText: true,
      blockAllMedia: true,
    }),
    // Promote console.error/console.assert into Sentry issues so UI-side failures
    // that were only logged (not thrown) still show up.
    Sentry.captureConsoleIntegration({ levels: ["error", "assert"] }),
    // Attach non-standard error properties (e.g. response bodies, codes) to events.
    Sentry.extraErrorDataIntegration({ depth: 5 }),
  ];

  Sentry.init({
    dsn,
    environment,
    release: import.meta.env.VITE_SENTRY_RELEASE?.trim() || undefined,
    integrations,
    tracesSampleRate: Number.isFinite(tracesSampleRate) ? tracesSampleRate : 0.2,
    // Propagate tracing headers to the local managed backend so a failed request can
    // be followed from the renderer into the Python span.
    tracePropagationTargets: ["localhost", "127.0.0.1", /:7348(\/|$)/],
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: import.meta.env.PROD ? 0.5 : 0,
    sendDefaultPii: false,
    // Browser SDK already auto-captures window.onerror + unhandledrejection.
    beforeSend(event) {
      if (event.request?.headers?.authorization) {
        delete event.request.headers.authorization;
      }
      return event;
    },
  });

  Sentry.setTag("runtime", isElectron ? "electron-renderer" : "web");

  initialized = true;
  return true;
}

export function captureSentryException(error, context = {}) {
  if (!getDsn()) return;
  if (!initialized) initSentry();
  Sentry.withScope((scope) => {
    Object.entries(context).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        scope.setExtra(key, value);
      }
    });
    Sentry.captureException(error);
  });
}

export function captureSentryMessage(message, level = "warning", context = {}) {
  if (!getDsn()) return;
  if (!initialized) initSentry();
  Sentry.withScope((scope) => {
    Object.entries(context).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        scope.setExtra(key, value);
      }
    });
    Sentry.captureMessage(message, level);
  });
}

export function setSentryUser({ userId, email, organizationId }) {
  if (!getDsn()) return;
  if (!initialized) initSentry();
  Sentry.setUser({
    id: userId ? String(userId) : undefined,
    email: email || undefined,
    organization_id: organizationId ? String(organizationId) : undefined,
  });
}

export function clearSentryUser() {
  if (!initialized) return;
  Sentry.setUser(null);
}

export { Sentry };
