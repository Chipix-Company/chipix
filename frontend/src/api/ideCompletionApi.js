import API_BASE_URL from "../config";

const DEFAULT_TIMEOUT_MS = 18000;

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function parseError(resp) {
  const err = await resp.json().catch(() => ({}));
  const detail = err.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => d.msg || d.message || String(d)).join("; ");
  }
  return `Request failed (${resp.status})`;
}

function mergeAbortSignals(signals = []) {
  const valid = signals.filter(Boolean);
  if (!valid.length) return undefined;
  if (valid.length === 1) return valid[0];
  const controller = new AbortController();
  const onAbort = () => controller.abort();
  valid.forEach((signal) => {
    if (signal.aborted) {
      controller.abort();
      return;
    }
    signal.addEventListener("abort", onAbort, { once: true });
  });
  return controller.signal;
}

export async function requestCompletion(
  projectId,
  payload,
  authToken,
  { signal, timeoutMs = DEFAULT_TIMEOUT_MS } = {},
) {
  const timeoutController = new AbortController();
  const timer = setTimeout(() => timeoutController.abort(), timeoutMs);
  const merged = mergeAbortSignals([signal, timeoutController.signal]);

  try {
    const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
      body: JSON.stringify(payload),
      signal: merged,
    });
    if (!resp.ok) {
      throw new Error(await parseError(resp));
    }
    const body = await resp.json();
    const serverLatency = resp.headers.get("x-completion-latency-ms");
    if (serverLatency && body && typeof body === "object") {
      body._serverLatencyMs = Number(serverLatency);
    }
    return body;
  } catch (err) {
    if (timeoutController.signal.aborted && !(signal?.aborted)) {
      throw new Error(`Completion timed out after ${timeoutMs}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export async function requestCompletionStream(
  projectId,
  payload,
  authToken,
  { signal, onChunk, timeoutMs = DEFAULT_TIMEOUT_MS } = {},
) {
  const timeoutController = new AbortController();
  const timer = setTimeout(() => timeoutController.abort(), timeoutMs);
  const merged = mergeAbortSignals([signal, timeoutController.signal]);

  try {
    const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
      body: JSON.stringify({ ...payload, stream: true }),
      signal: merged,
    });
    if (!resp.ok) {
      throw new Error(await parseError(resp));
    }
    if (!resp.body) {
      throw new Error("Streaming not supported");
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let last = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        const json = line.slice(5).trim();
        if (!json) continue;
        const event = JSON.parse(json);
        if (event.error) throw new Error(event.error);
        last = event;
        onChunk?.(event);
      }
    }
    return last;
  } catch (err) {
    if (timeoutController.signal.aborted && !(signal?.aborted)) {
      throw new Error(`Completion stream timed out after ${timeoutMs}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export async function postCompletionEvent(projectId, payload, authToken) {
  const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/completion-events`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    return false;
  }
  return true;
}
