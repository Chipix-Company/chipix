function sseDataLine(code, payload) {
  return `${code}:${JSON.stringify(payload)}\n`;
}

function mapSseJsonEventToDataLines(event = {}) {
  if (event.type === "text-delta") {
    return [sseDataLine(0, event.delta || "")];
  }
  if (event.type === "error") {
    const message = event.error?.message || event.message || "Stream error";
    return [sseDataLine(3, message)];
  }
  if (event.type === "tool-input-available") {
    return [sseDataLine(9, {
      toolCallId: event.toolCallId,
      toolName: event.toolName,
      args: event.input || event.args || {},
    })];
  }
  return [];
}

function normalizeFinishReason(reason) {
  const normalized = String(reason || "").replace(/_/g, "-");
  return ["stop", "length", "tool-calls", "content-filter", "error"].includes(normalized)
    ? normalized
    : "stop";
}

function buildStreamFallbackUrl(url) {
  try {
    const parsed = new URL(url);
    if (parsed.hostname === "127.0.0.1" && parsed.port === "7348") {
      return "";
    }
    parsed.hostname = "127.0.0.1";
    parsed.port = "7348";
    parsed.protocol = "http:";
    return parsed.toString();
  } catch {
    return "";
  }
}

export const __TEST_ONLY__ = {
  mapSseJsonEventToDataLines,
  normalizeFinishReason,
  buildStreamFallbackUrl,
};
