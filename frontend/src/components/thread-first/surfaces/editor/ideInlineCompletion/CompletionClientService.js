import { requestCompletion, requestCompletionStream } from "../../../../../api/ideCompletionApi";
import { buildCompletionRequest } from "./buildCompletionRequest";
import { CompletionDebouncer } from "./CompletionDebouncer";
import { CompletionMutex } from "./CompletionMutex";
import { ContextGatherer } from "./ContextGatherer";
import { CompletionTelemetry } from "./CompletionTelemetry";
import { SolutionCache } from "./SolutionCache";
import { cacheKeyForRequest, extractPrefixSuffix } from "./prefixSuffix";

export class CompletionClientService {
  constructor(options = {}) {
    this.projectId = options.projectId;
    this.authToken = options.authToken;
    this.enabled = options.enabled !== false;
    this.useStreaming = Boolean(options.useStreaming);
    this.rtlArtifactId = options.rtlArtifactId;
    this.specArtifactId = options.specArtifactId;
    this.mentalModelRevisionId = options.mentalModelRevisionId;
    this.ideFiles = options.ideFiles || [];
    this.revisionMap = options.revisionMap || {};
    this.getActiveFile = options.getActiveFile || (() => null);
    this.getIdeFiles = options.getIdeFiles || null;
    this.getRevisionMap = options.getRevisionMap || null;
    this.debug = options.debug || null;
    this.fetchTimeoutMs = options.fetchTimeoutMs || 18000;

    this.debouncer = new CompletionDebouncer();
    this.mutex = new CompletionMutex();
    this.cache = new SolutionCache();
    this.contextGatherer = new ContextGatherer();
    this.telemetry = new CompletionTelemetry({
      projectId: this.projectId,
      authToken: this.authToken,
    });
    this._lastCompletionId = null;
    this._lastShownAt = 0;
    /** @type {Map<string, Promise<object|null>>} */
    this._inFlight = new Map();
  }

  updateOptions(options = {}) {
    if (Object.prototype.hasOwnProperty.call(options, "projectId")) {
      this.projectId = options.projectId;
    }
    if (Object.prototype.hasOwnProperty.call(options, "authToken")) {
      this.authToken = options.authToken;
    }
    if (Object.prototype.hasOwnProperty.call(options, "enabled")) {
      this.enabled = options.enabled !== false;
    }
    if (Object.prototype.hasOwnProperty.call(options, "useStreaming")) {
      this.useStreaming = Boolean(options.useStreaming);
    }
    if (Object.prototype.hasOwnProperty.call(options, "rtlArtifactId")) {
      this.rtlArtifactId = options.rtlArtifactId;
    }
    if (Object.prototype.hasOwnProperty.call(options, "specArtifactId")) {
      this.specArtifactId = options.specArtifactId;
    }
    if (Object.prototype.hasOwnProperty.call(options, "mentalModelRevisionId")) {
      this.mentalModelRevisionId = options.mentalModelRevisionId;
    }
    if (Object.prototype.hasOwnProperty.call(options, "ideFiles")) {
      this.ideFiles = options.ideFiles || [];
    }
    if (Object.prototype.hasOwnProperty.call(options, "revisionMap")) {
      this.revisionMap = options.revisionMap || {};
    }
    if (Object.prototype.hasOwnProperty.call(options, "getActiveFile")) {
      this.getActiveFile = options.getActiveFile || (() => null);
    }
    if (Object.prototype.hasOwnProperty.call(options, "getIdeFiles")) {
      this.getIdeFiles = options.getIdeFiles || null;
    }
    if (Object.prototype.hasOwnProperty.call(options, "getRevisionMap")) {
      this.getRevisionMap = options.getRevisionMap || null;
    }
    if (Object.prototype.hasOwnProperty.call(options, "debug")) {
      this.debug = options.debug || null;
    }
    if (Object.prototype.hasOwnProperty.call(options, "fetchTimeoutMs")) {
      this.fetchTimeoutMs = options.fetchTimeoutMs || 18000;
    }
    this.telemetry.updateAuth(this.projectId, this.authToken);
  }

  invalidateFile(filepath) {
    this.cache.invalidateForFile(filepath);
  }

  dispose() {
    this.debouncer.cancel();
    this.mutex.abort();
    this._inFlight.clear();
  }

  _revisionKey(activeFile, revisionMap) {
    if (!activeFile) return "";
    const id = activeFile.id;
    const path = activeFile.path || activeFile.name || "";
    return String(revisionMap[id] ?? revisionMap[path] ?? "");
  }

  _toMonacoItem(item, position, monaco) {
    return {
      ...item,
      range: new monaco.Range(
        position.lineNumber,
        position.column,
        position.lineNumber,
        position.column,
      ),
    };
  }

  async fetchCompletion(model, position, monaco, ctx = {}) {
    if (!this.enabled) {
      this.debug?.skip("Tab complete disabled");
      return null;
    }
    if (!this.projectId) {
      this.debug?.skip("No projectId");
      return null;
    }
    if (!this.authToken) {
      this.debug?.skip("No auth token");
      return null;
    }

    const activeFile = this.getActiveFile();
    if (!activeFile) {
      this.debug?.skip("No active file");
      return null;
    }

    const revisionMap = this.getRevisionMap?.() || this.revisionMap || {};
    const revisionKey = this._revisionKey(activeFile, revisionMap);

    const extracted = ctx.prefix != null && ctx.suffix != null
      ? {
        prefix: ctx.prefix,
        suffix: ctx.suffix,
        cursorLine: position.lineNumber,
        cursorColumn: position.column,
      }
      : extractPrefixSuffix(model, position);

    const { prefix, suffix, cursorLine, cursorColumn } = extracted;
    const filepath = activeFile.path || activeFile.name || "";
    const language = activeFile.language || "";

    const forward = this.cache.tryForward({
      prefix,
      suffix,
      filepath,
      language,
      revisionKey,
    });
    if (forward) {
      this.debug?.cached(0, forward.insertText);
      return this._toMonacoItem(forward, position, monaco);
    }

    const cacheKey = cacheKeyForRequest({
      prefix,
      suffix,
      filepath,
      language,
      revisionKey,
    });
    const cached = this.cache.get(cacheKey);
    if (cached) {
      this.debug?.cached(0, cached.insertText);
      return this._toMonacoItem(cached, position, monaco);
    }

    if (ctx.monacoToken?.isCancellationRequested) {
      return null;
    }

    const existing = this._inFlight.get(cacheKey);
    if (existing) {
      try {
        const shared = await existing;
        if (ctx.monacoToken?.isCancellationRequested) return null;
        if (!shared) return null;
        return this._toMonacoItem(shared, position, monaco);
      } catch {
        return null;
      }
    }

    const flight = this._runFetch({
      model,
      position,
      monaco,
      activeFile,
      prefix,
      suffix,
      cursorLine,
      cursorColumn,
      filepath,
      language,
      revisionKey,
      cacheKey,
      monacoToken: ctx.monacoToken,
    });
    this._inFlight.set(cacheKey, flight);
    try {
      const result = await flight;
      if (ctx.monacoToken?.isCancellationRequested) return null;
      return result;
    } finally {
      if (this._inFlight.get(cacheKey) === flight) {
        this._inFlight.delete(cacheKey);
      }
    }
  }

  async _runFetch({
    position,
    monaco,
    activeFile,
    prefix,
    suffix,
    cursorLine,
    cursorColumn,
    filepath,
    language,
    revisionKey,
    cacheKey,
    monacoToken,
  }) {
    const ideFiles = this.getIdeFiles?.() || this.ideFiles || [];
    const revisionMap = this.getRevisionMap?.() || this.revisionMap || {};

    const context = this.contextGatherer.gather({
      activeFile,
      ideFiles,
      revisionMap,
    });

    const payload = buildCompletionRequest({
      prefix,
      suffix,
      filepath,
      filename: activeFile.name,
      language,
      projectId: this.projectId,
      rtlArtifactId: this.rtlArtifactId,
      specArtifactId: this.specArtifactId,
      mentalModelRevisionId: this.mentalModelRevisionId,
      cursorLine,
      cursorColumn,
      context,
    });

    const started = performance.now();
    const signal = this.mutex.combinedSignal(monacoToken);
    this.debug?.sending({
      filepath,
      prefixLen: prefix.length,
      suffixLen: suffix.length,
    });

    try {
      let response;
      const fetchOpts = { signal, timeoutMs: this.fetchTimeoutMs };
      if (this.useStreaming) {
        response = await requestCompletionStream(
          this.projectId,
          payload,
          this.authToken,
          fetchOpts,
        );
      } else {
        response = await requestCompletion(
          this.projectId,
          payload,
          this.authToken,
          fetchOpts,
        );
      }

      if (signal.aborted || monacoToken?.isCancellationRequested) {
        return null;
      }

      const latencyMs = Math.round(performance.now() - started);
      const serverMs = response?.latency_ms || response?._serverLatencyMs;
      this.debouncer.noteLatency(serverMs || latencyMs);

      const text = response?.choices?.[0]?.text || "";
      if (!text.trim()) {
        this.debug?.empty(latencyMs);
        return null;
      }

      const item = {
        insertText: text,
        completionId: response.id,
        filepath,
        language,
      };
      this.cache.set(cacheKey, item, { filepath, prefix });
      this._lastCompletionId = response.id;
      this._lastShownAt = Date.now();
      this.debug?.success(latencyMs, text, { id: response.id });
      void this.telemetry.view(response.id, { filepath, language, elapsedMs: latencyMs });
      return this._toMonacoItem(item, position, monaco);
    } catch (err) {
      const latencyMs = Math.round(performance.now() - started);
      if (err?.name === "AbortError" || signal.aborted) {
        return null;
      }
      this.debug?.error(err?.message || String(err), latencyMs);
      return null;
    }
  }
}
