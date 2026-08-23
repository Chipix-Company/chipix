/**
 * Merge streamed assistant text with a terminal `done` payload.
 * The backend sends incremental deltas, then `done` may repeat the full
 * accumulated message — appending would duplicate ```chipix:ask``` fences.
 */
export function mergeAssistantTailText(current = "", tail = "") {
  const cur = String(current || "");
  const next = String(tail || "");
  if (!next) return cur;
  if (!cur) return next;
  if (next === cur) return cur;
  if (next.startsWith(cur)) return next;
  if (cur.startsWith(next)) return cur;
  if (cur.includes(next)) return cur;
  return cur + next;
}
