export function shouldProcessToolCall(handledToolCalls, toolCallId, maxEntries = 250) {
  if (!toolCallId || handledToolCalls.has(toolCallId)) {
    return false;
  }
  if (handledToolCalls.size >= maxEntries) {
    handledToolCalls.clear();
  }
  handledToolCalls.add(toolCallId);
  return true;
}
