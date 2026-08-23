/** Live debug state for inline completion (status bar + optional console). */

const MAX_LOG = 12;

export function createCompletionDebug({ onChange } = {}) {
  let state = {
    phase: "idle",
    detail: "",
    lastLatencyMs: null,
    debounceMs: null,
    lastError: null,
    lastPreview: "",
    requestCount: 0,
    cacheHits: 0,
    updatedAt: Date.now(),
    log: [],
  };

  const notify = () => {
    if (typeof requestAnimationFrame === "undefined") {
      onChange?.({ ...state, log: [...state.log] });
      return;
    }
    if (notify._scheduled) return;
    notify._scheduled = true;
    requestAnimationFrame(() => {
      notify._scheduled = false;
      onChange?.({ ...state, log: [...state.log] });
    });
  };

  const pushLog = (entry) => {
    state.log = [{ ...entry, at: Date.now() }, ...state.log].slice(0, MAX_LOG);
  };

  const api = {
    getState: () => ({ ...state, log: [...state.log] }),

    reset() {
      state = {
        ...state,
        phase: "idle",
        detail: "",
        lastError: null,
        lastPreview: "",
        updatedAt: Date.now(),
      };
      notify();
    },

    trigger(meta = {}) {
      state = {
        ...state,
        phase: "triggered",
        detail: "Monaco asked for suggestion",
        lastError: null,
        updatedAt: Date.now(),
      };
      pushLog({ kind: "trigger", ...meta });
      notify();
    },

    skip(reason, meta = {}) {
      state = {
        ...state,
        phase: "skipped",
        detail: reason,
        updatedAt: Date.now(),
      };
      pushLog({ kind: "skip", reason, ...meta });
      notify();
    },

    debounce(ms) {
      state = {
        ...state,
        phase: "debouncing",
        detail: `Waiting ${ms}ms`,
        debounceMs: ms,
        updatedAt: Date.now(),
      };
      pushLog({ kind: "debounce", ms });
      notify();
    },

    sending(meta = {}) {
      state = {
        ...state,
        phase: "sending",
        detail: "POST /completions",
        requestCount: state.requestCount + 1,
        lastError: null,
        updatedAt: Date.now(),
      };
      pushLog({ kind: "send", ...meta });
      notify();
    },

    cached(latencyMs, preview) {
      state = {
        ...state,
        phase: "cached",
        detail: "LRU cache hit",
        lastLatencyMs: latencyMs,
        lastPreview: preview || "",
        cacheHits: state.cacheHits + 1,
        updatedAt: Date.now(),
      };
      pushLog({ kind: "cached", latencyMs, preview });
      notify();
    },

    success(latencyMs, preview, meta = {}) {
      state = {
        ...state,
        phase: "ok",
        detail: preview ? `Ghost: ${preview.slice(0, 40)}` : "Empty response",
        lastLatencyMs: latencyMs,
        lastPreview: preview || "",
        lastError: null,
        updatedAt: Date.now(),
      };
      pushLog({ kind: "ok", latencyMs, preview, ...meta });
      notify();
    },

    empty(latencyMs, meta = {}) {
      state = {
        ...state,
        phase: "empty",
        detail: "API returned no text",
        lastLatencyMs: latencyMs,
        updatedAt: Date.now(),
      };
      pushLog({ kind: "empty", latencyMs, ...meta });
      notify();
    },

    error(message, latencyMs = null, meta = {}) {
      state = {
        ...state,
        phase: "error",
        detail: message || "Request failed",
        lastError: message || "Request failed",
        lastLatencyMs: latencyMs,
        updatedAt: Date.now(),
      };
      pushLog({ kind: "error", message, latencyMs, ...meta });
      notify();
    },

    aborted() {
      state = {
        ...state,
        phase: "aborted",
        detail: "Superseded by newer keystroke",
        updatedAt: Date.now(),
      };
      pushLog({ kind: "aborted" });
      notify();
    },
  };

  notify();
  return api;
}

export function formatCompletionDebugLine(state) {
  if (!state) return "Complete: idle · — · req 0";
  const lat = state.lastLatencyMs != null ? `${state.lastLatencyMs}ms` : "—";
  const phase = state.phase || "idle";
  const debounce = state.debounceMs != null ? ` · deb ${state.debounceMs}ms` : "";
  const detail = state.phase === "error" && state.lastError
    ? ` · ${String(state.lastError).slice(0, 48)}`
    : state.phase === "skipped" && state.detail
      ? ` · ${String(state.detail).slice(0, 48)}`
      : "";
  return `Complete: ${phase} · ${lat}${debounce} · req ${state.requestCount || 0}${detail}`;
}
