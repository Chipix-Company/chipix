/**
 * Helpers for the live "agent is writing" code peek panel.
 */

export function inferFileLanguage(filename = "") {
  const lower = String(filename).toLowerCase();
  if (lower.endsWith(".sv") || lower.endsWith(".svh")) return "systemverilog";
  if (lower.endsWith(".v") || lower.endsWith(".vh")) return "verilog";
  if (lower.endsWith(".py")) return "python";
  if (lower.endsWith(".tcl")) return "tcl";
  if (lower.endsWith(".md")) return "markdown";
  return "text";
}

/** Best-effort unescape of a partial JSON string value. */
export function unescapePartialJsonString(raw) {
  if (!raw) return "";
  try {
    return JSON.parse(`"${raw}"`);
  } catch {
    return String(raw)
      .replace(/\\n/g, "\n")
      .replace(/\\r/g, "\r")
      .replace(/\\t/g, "\t")
      .replace(/\\"/g, '"')
      .replace(/\\\\/g, "\\");
  }
}

/**
 * Parse in-flight file content from the assistant stream buffer
 * (before tool_call_started may arrive).
 */
export function extractInFlightFileWrite(text) {
  if (!text || typeof text !== "string") return null;

  const genMatch = text.match(
    /<GENERATEDCODE\s+lang=["']?([^"'>]+)["']?\s*>([\s\S]*?)(?:<\/GENERATEDCODE>|$)/i,
  );
  if (genMatch) {
    return {
      filename: null,
      content: genMatch[2].trim(),
      language: genMatch[1] || "text",
    };
  }

  const idx = text.lastIndexOf("createFile");
  if (idx < 0) return null;

  const slice = text.slice(idx);
  const fnMatch = slice.match(/"filename"\s*:\s*"([^"\\]+)"/);
  if (!fnMatch) return null;

  const filename = fnMatch[1];
  const contentMatch = slice.match(/"content"\s*:\s*"((?:[^"\\]|\\.)*)(?:"|$)/s);
  const content = contentMatch
    ? unescapePartialJsonString(contentMatch[1])
    : "";

  return {
    filename,
    content,
    language: inferFileLanguage(filename),
  };
}

export function liveWriteFromToolArgs(tool, args = {}) {
  if (tool === "createFile") {
    const filename = args.filename || args.path || null;
    const content = args.content ?? "";
    if (!filename) return null;
    return {
      filename,
      content: String(content),
      language: inferFileLanguage(filename),
      tool: "createFile",
      phase: "writing",
    };
  }
  if (tool === "applyCodeToFile") {
    const filename = args.filename || args.path || "edited file";
    const content = args.code ?? args.content ?? "";
    return {
      filename,
      content: String(content),
      language: inferFileLanguage(filename),
      tool: "applyCodeToFile",
      phase: "writing",
    };
  }
  return null;
}
