/** Single-flight abort — Tabby tabby-agent CompletionMutex pattern. */

export class CompletionMutex {
  constructor() {
    this._controller = null;
  }

  abort() {
    if (this._controller) {
      this._controller.abort();
      this._controller = null;
    }
  }

  begin() {
    this.abort();
    this._controller = new AbortController();
    return this._controller.signal;
  }

  /**
   * Merge mutex signal with Monaco cancellation token (Tabby: LSP cancel + HTTP abort).
   */
  combinedSignal(monacoToken) {
    const mutexSignal = this.begin();
    if (!monacoToken) return mutexSignal;

    const merged = new AbortController();
    const abortMerged = () => {
      if (!merged.signal.aborted) merged.abort();
    };

    mutexSignal.addEventListener("abort", abortMerged);
    if (monacoToken.isCancellationRequested) {
      abortMerged();
      return merged.signal;
    }
    if (typeof monacoToken.onCancellationRequested === "function") {
      monacoToken.onCancellationRequested(abortMerged);
    }

    return merged.signal;
  }
}
