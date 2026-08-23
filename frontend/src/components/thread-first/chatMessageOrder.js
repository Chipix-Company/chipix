const ROLE_RANK = { user: 0, assistant: 1, tool: 2 };

/** Chronological order for API message rows (guards same-ms created_at + UUID ties). */
export function sortChatMessagesChronologically(messages) {
  const rows = Array.isArray(messages) ? [...messages] : [];
  return rows.sort((a, b) => {
    const ta = Date.parse(a?.created_at || "") || 0;
    const tb = Date.parse(b?.created_at || "") || 0;
    if (ta !== tb) return ta - tb;
    const ra = ROLE_RANK[a?.role] ?? 9;
    const rb = ROLE_RANK[b?.role] ?? 9;
    if (ra !== rb) return ra - rb;
    return String(a?.id || "").localeCompare(String(b?.id || ""));
  });
}
