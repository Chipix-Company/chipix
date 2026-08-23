export function inferMonacoLanguage(filename = "") {
  const lower = String(filename || "").toLowerCase();
  if (lower.endsWith(".sv") || lower.endsWith(".svh") || lower.endsWith(".v") || lower.endsWith(".vh")) {
    return "systemverilog";
  }
  if (lower.endsWith(".json")) return "json";
  if (lower.endsWith(".md") || lower.endsWith(".markdown")) return "markdown";
  if (lower.endsWith(".py")) return "python";
  if (lower.endsWith(".js") || lower.endsWith(".mjs") || lower.endsWith(".cjs")) return "javascript";
  if (lower.endsWith(".ts") || lower.endsWith(".tsx")) return "typescript";
  if (lower.endsWith(".yaml") || lower.endsWith(".yml")) return "yaml";
  if (lower.endsWith(".html") || lower.endsWith(".htm")) return "html";
  if (lower.endsWith(".css")) return "css";
  if (lower.endsWith(".txt")) return "plaintext";
  return "plaintext";
}

export function getEditorFileKind(filename = "") {
  if (/\.pdf$/i.test(filename || "")) return "pdf";
  return "code";
}

export function isRtlFile(name = "") {
  return /\.(sv|svh|v|vh)$/i.test(name || "");
}

export function isCompletableFile(name = "") {
  return isRtlFile(name);
}

export function backendCompletionLanguage(filename = "", monacoLanguage = "") {
  const lower = String(filename || "").toLowerCase();
  if (lower.endsWith(".v") || lower.endsWith(".vh")) return "verilog";
  if (/\.(sv|svh)$/i.test(lower)) return "systemverilog";
  if (String(monacoLanguage || "").toLowerCase() === "verilog") return "verilog";
  return "systemverilog";
}

export function fileGlyph(name = "") {
  const lower = String(name || "").toLowerCase();
  if (lower.endsWith(".pdf")) return "PDF";
  if (lower.endsWith(".sv") || lower.endsWith(".svh")) return "SV";
  if (lower.endsWith(".v") || lower.endsWith(".vh")) return "V";
  if (lower.endsWith(".md")) return "MD";
  if (lower.endsWith(".json")) return "JS";
  if (lower.endsWith(".py")) return "PY";
  const ext = lower.split(".").pop() || "";
  return ext.slice(0, 2).toUpperCase() || "??";
}

const INLINE_COMPLETION_STORAGE_KEY = "chipverify-ide-inline-completion-enabled";

export function isInlineCompletionEnabled() {
  try {
    const raw = localStorage.getItem(INLINE_COMPLETION_STORAGE_KEY);
    if (raw === null) return true;
    return raw !== "false";
  } catch {
    return true;
  }
}

export function setInlineCompletionEnabled(value) {
  try {
    localStorage.setItem(INLINE_COMPLETION_STORAGE_KEY, value ? "true" : "false");
  } catch {
    // ignore
  }
}
