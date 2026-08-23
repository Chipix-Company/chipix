/**
 * UserJot feedback widget — CDN loader + window.uj proxy.
 * @see https://userjot.com/docs/widget-installation
 */

const SDK_URL = "https://cdn.userjot.com/sdk/v2/uj.js";

let loaderPromise = null;
let initialized = false;

export function isUserJotConfigured() {
  return Boolean(import.meta.env.VITE_USERJOT_PROJECT_ID?.trim());
}

function ensureProxy() {
  if (typeof window === "undefined") return;
  window.$ujq = window.$ujq || [];
  if (!window.uj) {
    window.uj = new Proxy(
      {},
      {
        get: (_, prop) => (...args) => {
          window.$ujq.push([prop, ...args]);
        },
      },
    );
  }
}

/** Idempotent SDK loader — safe to call from React useEffect. */
export function loadUserJot() {
  if (typeof window === "undefined") return Promise.resolve();
  if (loaderPromise) return loaderPromise;

  ensureProxy();

  const existing = document.querySelector(`script[src="${SDK_URL}"]`);
  if (existing) {
    loaderPromise = Promise.resolve();
    return loaderPromise;
  }

  loaderPromise = new Promise((resolve) => {
    const script = document.createElement("script");
    script.src = SDK_URL;
    script.type = "module";
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => resolve();
    document.head.appendChild(script);
  });

  return loaderPromise;
}

/**
 * Initialize UserJot with custom trigger (no floating bubble — sidebar opens it).
 * @see https://userjot.com/docs/widget-configuration
 */
export async function initUserJot(options = {}) {
  const projectId = import.meta.env.VITE_USERJOT_PROJECT_ID?.trim();
  if (!projectId || initialized) return;

  await loadUserJot();

  return new Promise((resolve) => {
    window.uj.init(projectId, {
      widget: true,
      theme: "auto",
      position: "right",
      trigger: "custom",
      ...options,
      onReady: () => {
        initialized = true;
        options.onReady?.();
        resolve();
      },
      onError: (error) => {
        options.onError?.(error);
        resolve();
      },
    });
  });
}

/** @see https://userjot.com/docs/identify-users */
export function identifyUserJotUser(user) {
  if (!window.uj || !isUserJotConfigured()) return;
  if (!user) {
    window.uj.identify(null);
    return;
  }
  window.uj.identify({
    id: String(user.id),
    email: user.email || undefined,
    firstName: user.first_name || user.firstName || undefined,
    lastName: user.last_name || user.lastName || undefined,
    avatar: user.avatar_url || user.avatarUrl || undefined,
    traits: user.traits || undefined,
    companies: user.companies || undefined,
  });
}

/** @see https://userjot.com/docs/widget-sdk-reference */
export function openUserJot(section = "feedback") {
  if (!window.uj) return;
  window.uj.showWidget(section ? { section } : undefined);
}

export function hideUserJot() {
  window.uj?.hideWidget?.();
}

/** Open public board in a new tab with feedback modal. @see https://userjot.com/docs/deep-link-feedback */
export function openUserJotPublicBoard({ section = "feedback", openFeedback = true } = {}) {
  const base = import.meta.env.VITE_USERJOT_PUBLIC_URL?.trim();
  if (base) {
    const url = new URL(base);
    if (openFeedback && section === "feedback") url.searchParams.set("openFeedback", "true");
    else if (section === "roadmap") url.pathname = `${url.pathname.replace(/\/$/, "")}/roadmap`;
    else if (section === "updates") url.pathname = `${url.pathname.replace(/\/$/, "")}/updates`;
    window.open(url.toString(), "_blank", "noopener,noreferrer");
    return;
  }
  window.uj?.redirect?.({
    to: section,
    openFeedback: openFeedback && section === "feedback",
    newTab: true,
  });
}

export function setUserJotTheme(theme) {
  window.uj?.setTheme?.(theme);
}
