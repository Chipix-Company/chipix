import posthog from "posthog-js";

let initialized = false;

function isEnabled() {
  return Boolean(import.meta.env.VITE_POSTHOG_KEY?.trim());
}

export function initPostHog() {
  if (initialized || typeof window === "undefined") return null;
  const apiKey = import.meta.env.VITE_POSTHOG_KEY?.trim();
  if (!apiKey) return null;

  const host = import.meta.env.VITE_POSTHOG_HOST?.trim() || "https://us.i.posthog.com";
  const isDev = import.meta.env.DEV;

  posthog.init(apiKey, {
    api_host: host,
    person_profiles: "identified_only",
    capture_pageview: false,
    capture_pageleave: true,
    autocapture: false,
    persistence: "localStorage",
    opt_out_capturing_by_default: import.meta.env.VITE_POSTHOG_OPT_OUT === "true",
    loaded: (client) => {
      if (isDev) client.debug(false);
    },
  });

  initialized = true;
  return posthog;
}

export function getPostHog() {
  return initialized ? posthog : null;
}

export function identifyPostHogUser({ userId, email, organizationId, organizationName }) {
  if (!isEnabled() || !userId) return;
  const client = getPostHog() || initPostHog();
  if (!client) return;

  client.identify(String(userId), {
    email: email || undefined,
    organization_id: organizationId || undefined,
    organization_name: organizationName || undefined,
  });

  if (organizationId) {
    client.group("organization", String(organizationId), {
      name: organizationName || undefined,
    });
  }
}

export function resetPostHogUser() {
  if (!initialized) return;
  posthog.reset();
}

export function capturePostHog(event, properties = {}) {
  if (!isEnabled()) return;
  const client = getPostHog() || initPostHog();
  if (!client) return;
  client.capture(event, properties);
}

export function registerPostHogSuperProperties(properties = {}) {
  if (!isEnabled()) return;
  const client = getPostHog() || initPostHog();
  if (!client) return;
  client.register(properties);
}
