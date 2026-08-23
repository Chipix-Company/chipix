/**
 * Adaptive debounce — ported from Tabby tabby-agent debouncer.ts
 * (100–1000ms, context score, subtract server latency).
 */

const INVOKE_TRIGGER = 1; // monaco.languages.InlineCompletionTriggerKind.Invoke

export class CompletionDebouncer {
  constructor({ minMs = 100, maxMs = 1000 } = {}) {
    this.minMs = minMs;
    this.maxMs = maxMs;
    this._keystrokeTimes = [];
    this._lastServerLatencyMs = 0;
    this._timer = null;
    this._generation = 0;
  }

  recordKeystroke() {
    const now = Date.now();
    this._keystrokeTimes.push(now);
    if (this._keystrokeTimes.length > 10) {
      this._keystrokeTimes.shift();
    }
  }

  noteLatency(ms) {
    if (Number.isFinite(ms) && ms > 0) {
      this._lastServerLatencyMs = ms;
    }
  }

  _averageIntervalMs() {
    if (this._keystrokeTimes.length < 2) return 250;
    let sum = 0;
    for (let i = 1; i < this._keystrokeTimes.length; i += 1) {
      sum += this._keystrokeTimes[i] - this._keystrokeTimes[i - 1];
    }
    const avg = sum / (this._keystrokeTimes.length - 1);
    return Math.max(100, Math.min(400, avg));
  }

  _contextScore(prefix = "") {
    const line = String(prefix).split("\n").pop() || "";
    let score = 1.0;
    if (/[\s.;,({[=:]$/.test(line)) score *= 1.5;
    if (line.trim().length === 0) score *= 1.2;
    return Math.min(3.0, Math.max(1.0, score));
  }

  computeDelayMs(triggerKind, prefix = "", suffix = "") {
    if (triggerKind === INVOKE_TRIGGER) return 0;

    const base = this._averageIntervalMs();
    const scored = Math.round(base * this._contextScore(prefix));
    const adjusted = scored - Math.round(this._lastServerLatencyMs * 0.5);
    return Math.max(this.minMs, Math.min(this.maxMs, adjusted));
  }

  cancel() {
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
    this._generation += 1;
  }

  /**
   * Tabby wait(): manual trigger = 0ms; adaptive = computed delay.
   * Returns a promise that resolves after the delay (or immediately).
   */
  wait(triggerKind, prefix, suffix, onScheduled) {
    this.cancel();
    const runId = this._generation;
    const delay = this.computeDelayMs(triggerKind, prefix, suffix);
    onScheduled?.(delay);

    if (delay <= 0) {
      return Promise.resolve(delay);
    }

    return new Promise((resolve) => {
      this._timer = setTimeout(() => {
        this._timer = null;
        if (runId !== this._generation) {
          resolve(-1);
          return;
        }
        resolve(delay);
      }, delay);
    });
  }
}
