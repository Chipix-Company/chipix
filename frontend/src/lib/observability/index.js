import { AnalyticsEvents } from "./events";
import {
  capturePostHog,
  identifyPostHogUser,
  initPostHog,
  registerPostHogSuperProperties,
  resetPostHogUser,
} from "./posthog";
import { initUserJot, identifyUserJotUser } from "../userjot";
import {
  captureSentryException,
  captureSentryMessage,
  clearSentryUser,
  initSentry,
  setSentryUser,
  Sentry,
} from "./sentry";

export { AnalyticsEvents, Sentry };

export function initObservability() {
  initSentry();
  initPostHog();
  void initUserJot();

  const platform = typeof window !== "undefined" && window.electronAPI ? "electron" : "web";
  const envVersion = import.meta.env.VITE_APP_VERSION?.trim() || "beta";
  registerPostHogSuperProperties({
    platform,
    app: "chipverify-studio",
    app_version: envVersion,
  });

  if (typeof window !== "undefined" && window.electronAPI?.getAppVersion) {
    void window.electronAPI.getAppVersion().then((runtimeVersion) => {
      if (typeof runtimeVersion === "string" && runtimeVersion.trim()) {
        registerPostHogSuperProperties({ app_version: runtimeVersion.trim() });
      }
    });
  }

  track(AnalyticsEvents.APP_STARTED, { platform });
}

export function identifyUser({ user, organization } = {}) {
  const userId = user?.id;
  if (!userId) return;

  identifyPostHogUser({
    userId,
    email: user?.email,
    organizationId: organization?.id,
    organizationName: organization?.name,
  });

  setSentryUser({
    userId,
    email: user?.email,
    organizationId: organization?.id,
  });

  identifyUserJotUser({
    id: userId,
    email: user?.email,
    first_name: user?.first_name || user?.name?.split?.(" ")?.[0],
    last_name: user?.last_name || user?.name?.split?.(" ")?.slice(1)?.join(" "),
    companies: organization?.id
      ? [{ id: String(organization.id), name: organization.name }]
      : undefined,
  });
}

export function resetObservabilityUser() {
  resetPostHogUser();
  clearSentryUser();
  identifyUserJotUser(null);
}

export function setAnalyticsContext(context = {}) {
  const props = {};
  if (context.projectId) props.project_id = String(context.projectId);
  if (context.threadId) props.thread_id = String(context.threadId);
  if (context.runId) props.run_id = String(context.runId);
  if (context.chipixMode) props.chipix_mode = String(context.chipixMode);
  if (context.organizationId) props.organization_id = String(context.organizationId);
  if (Object.keys(props).length) registerPostHogSuperProperties(props);
}

export function track(event, properties = {}) {
  capturePostHog(event, properties);
}

export function captureError(error, context = {}) {
  captureSentryException(error, context);
  if (context.reportToAnalytics !== false) {
    capturePostHog("error.captured", {
      message: error?.message || String(error),
      ...context,
    });
  }
}

export function captureMessage(message, level = "warning", context = {}) {
  captureSentryMessage(message, level, context);
}
