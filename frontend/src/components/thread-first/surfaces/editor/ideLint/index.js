import { requestLint } from "../../../../../api/ideLintApi";
import { isRtlFile } from "../ideEditorUtils";

const DEBOUNCE_MS = 400;

function severityToMarker(monaco, severity) {
  const value = String(severity || "").toLowerCase();
  if (value === "error") return monaco.MarkerSeverity.Error;
  if (value === "hint") return monaco.MarkerSeverity.Hint;
  if (value === "information" || value === "info") return monaco.MarkerSeverity.Info;
  return monaco.MarkerSeverity.Warning;
}

function diagnosticsToMarkers(monaco, diagnostics = []) {
  return diagnostics.map((diag) => ({
    severity: severityToMarker(monaco, diag.severity),
    message: diag.message || "lint issue",
    startLineNumber: (diag.line ?? 0) + 1,
    startColumn: (diag.col ?? 0) + 1,
    endLineNumber: (diag.endLine ?? diag.line ?? 0) + 1,
    endColumn: Math.max((diag.endCol ?? diag.col ?? 0) + 1, 1),
    source: diag.rule || "svls",
  }));
}

export function registerIdeLint(monaco, editor, options = {}) {
  let disposed = false;
  let timer = null;
  let requestId = 0;

  const runLint = async () => {
    const {
      projectId,
      authToken,
      filename,
      filepath,
      getContent,
      onUnavailable,
    } = options;
    if (!projectId || !filename || !isRtlFile(filename)) {
      const model = editor.getModel();
      if (model) monaco.editor.setModelMarkers(model, "svls", []);
      return;
    }

    const content = typeof getContent === "function" ? getContent() : editor.getValue();
    const currentId = ++requestId;
    try {
      const result = await requestLint(
        projectId,
        { filepath: filepath || filename, content, version: currentId },
        authToken,
      );
      if (disposed || currentId !== requestId) return;
      const model = editor.getModel();
      if (!model) return;
      if (result?.tool_available === false) {
        monaco.editor.setModelMarkers(model, "svls", []);
        onUnavailable?.(result?.skip_reason || "svls unavailable");
        return;
      }
      monaco.editor.setModelMarkers(
        model,
        "svls",
        diagnosticsToMarkers(monaco, result?.diagnostics || []),
      );
    } catch {
      if (disposed || currentId !== requestId) return;
    }
  };

  const scheduleLint = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { void runLint(); }, DEBOUNCE_MS);
  };

  const contentDisposable = editor.onDidChangeModelContent(scheduleLint);
  scheduleLint();

  return {
    refresh: () => { void runLint(); },
    updateOptions(nextOptions) {
      Object.assign(options, nextOptions || {});
      scheduleLint();
    },
    dispose() {
      disposed = true;
      if (timer) clearTimeout(timer);
      contentDisposable.dispose();
      const model = editor.getModel();
      if (model) monaco.editor.setModelMarkers(model, "svls", []);
    },
  };
}
