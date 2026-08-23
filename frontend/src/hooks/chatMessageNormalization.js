export function normalizeChatMessage(message = {}, pendingToolCalls = []) {
  const textParts = Array.isArray(message.parts)
    ? message.parts.filter((part) => part?.type === "text" && typeof part.text === "string")
    : [];
  const content = textParts.length
    ? textParts.map((part) => part.text).join("")
    : String(message.content ?? "");
  const pendingIds = new Set(
    (Array.isArray(pendingToolCalls) ? pendingToolCalls : [])
      .map((call) => call?.toolCallId || call?.id)
      .filter(Boolean),
  );
  const toolCalls = (Array.isArray(message.parts) ? message.parts : [])
    .filter((part) => typeof part?.type === "string" && part.type.startsWith("tool-"))
    .map((part) => ({
      ...part,
      name: part.toolName || part.type.replace(/^tool-/, ""),
      args: part.input ?? part.args ?? {},
      status: pendingIds.has(part.toolCallId || part.id)
        ? "awaiting_confirmation"
        : part.state || "pending",
    }));

  return {
    ...message,
    content,
    role: message.role || "assistant",
    toolCalls,
  };
}
