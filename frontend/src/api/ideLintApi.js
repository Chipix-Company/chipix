import API_BASE_URL from "../config";

const DEFAULT_TIMEOUT_MS = 12000;

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function parseError(resp) {
  const err = await resp.json().catch(() => ({}));
  const detail = err.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    return detail.message || JSON.stringify(detail);
  }
  if (Array.isArray(detail)) {
    return detail.map((d) => d.msg || d.message || String(d)).join("; ");
  }
  return `Request failed (${resp.status})`;
}

export async function requestLint(
  projectId,
  payload,
  authToken,
  { signal, timeoutMs = DEFAULT_TIMEOUT_MS } = {},
) {
  const timeoutController = new AbortController();
  const timer = setTimeout(() => timeoutController.abort(), timeoutMs);
  const merged = signal
    ? (() => {
        const controller = new AbortController();
        const onAbort = () => controller.abort();
        if (signal.aborted) controller.abort();
        else signal.addEventListener("abort", onAbort, { once: true });
        timeoutController.signal.addEventListener("abort", onAbort, { once: true });
        return controller.signal;
      })()
    : timeoutController.signal;

  try {
    const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/lint`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
      body: JSON.stringify(payload),
      signal: merged,
    });
    if (resp.status === 503) {
      const body = await resp.json().catch(() => ({}));
      return {
        passed: true,
        tool_available: false,
        diagnostics: [],
        skipped: true,
        skip_reason: body?.detail?.message || "svls unavailable",
      };
    }
    if (!resp.ok) {
      throw new Error(await parseError(resp));
    }
    return resp.json();
  } finally {
    clearTimeout(timer);
  }
}
