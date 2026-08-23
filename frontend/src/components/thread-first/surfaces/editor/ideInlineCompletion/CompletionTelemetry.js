import { postCompletionEvent } from "../../../../../api/ideCompletionApi";

export class CompletionTelemetry {
  constructor({ projectId, authToken }) {
    this.projectId = projectId;
    this.authToken = authToken;
    this._viewed = new Set();
  }

  updateAuth(projectId, authToken) {
    this.projectId = projectId;
    this.authToken = authToken;
  }

  async view(completionId, meta = {}) {
    if (!completionId || this._viewed.has(completionId)) return;
    this._viewed.add(completionId);
    await this._send("view", completionId, meta);
  }

  async select(completionId, meta = {}) {
    await this._send("select", completionId, meta);
  }

  async dismiss(completionId, meta = {}) {
    await this._send("dismiss", completionId, meta);
  }

  async _send(type, completionId, meta) {
    if (!this.projectId || !completionId) return;
    try {
      await postCompletionEvent(
        this.projectId,
        {
          type,
          completion_id: completionId,
          choice_index: meta.choiceIndex ?? 0,
          choice_text: meta.choiceText,
          select_kind: meta.selectKind,
          filepath: meta.filepath,
          language: meta.language,
          elapsed_ms: meta.elapsedMs,
        },
        this.authToken,
      );
    } catch {
      // fail silent
    }
  }
}
