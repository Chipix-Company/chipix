const TOOL_ALIASES = {
  list_dir: "listFiles",
  list_files: "listFiles",
  read_file: "readFile",
  create_file: "createFile",
  apply_code_to_file: "applyCodeToFile",
};

function normalizeToolName(name) {
  const raw = String(name || "").trim();
  return TOOL_ALIASES[raw] || raw;
}

function normalizeArgsShape(args) {
  if (!args) return {};
  if (typeof args === "string") {
    try {
      const parsed = JSON.parse(args);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }
  return typeof args === "object" && !Array.isArray(args) ? { ...args } : {};
}

function inferArtifactType(filename = "") {
  const lower = filename.toLowerCase();
  if (/\.(sv|svh|v|vh)$/.test(lower)) return "rtl";
  if (/\.(txt|md|pdf|docx)$/.test(lower)) return "spec";
  return "generated";
}

function normalizeToolArgs(args) {
  const normalized = normalizeArgsShape(args);
  const filename = normalized.filename || normalized.file_name || normalized.path || "";
  if (filename && !normalized.filename) {
    normalized.filename = filename;
  }
  if (normalized.code && !normalized.content) {
    normalized.content = normalized.code;
  }
  if (!normalized.artifact_type && filename) {
    normalized.artifact_type = inferArtifactType(filename);
  }
  return normalized;
}

function normalizePathLike(value) {
  return String(value || "").replace(/\\/g, "/").toLowerCase();
}

function isLikelyArtifactId(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    String(value || ""),
  );
}

export const __TEST_ONLY__ = {
  normalizeToolName,
  normalizeArgsShape,
  normalizeToolArgs,
  normalizePathLike,
  isLikelyArtifactId,
};
