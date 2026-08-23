import { useCallback } from "react";
import API_BASE_URL from "../../config";
import { THREAD_KINDS } from "./types";

/**
 * Collect artifact IDs from FILE cards in a removed slice (for rewind API bookkeeping).
 */
export function collectArtifactIdsFromItems(items) {
  const ids = [];
  for (const it of items || []) {
    if (it?.kind === THREAD_KINDS.FILE && it.payload?.artifactId) {
      ids.push(String(it.payload.artifactId));
    }
  }
  return [...new Set(ids)];
}

/**
 * Split a merged timeline at a user turn. By default drops the pivot user bubble
 * so a resend can insert a fresh one with edited text.
 */
export function partitionTimelineAtUserTurn(items, turnId, { dropPivotUser = true } = {}) {
  if (!turnId || !Array.isArray(items)) {
    return { kept: items || [], removed: [], pivotIdx: -1 };
  }
  const pivotIdx = items.findIndex(
    (it) => it?.turnId === turnId && it?.kind === THREAD_KINDS.USER,
  );
  if (pivotIdx < 0) {
    return { kept: items, removed: [], pivotIdx: -1 };
  }
  const cutFrom = dropPivotUser ? pivotIdx : pivotIdx + 1;
  return {
    kept: items.slice(0, cutFrom),
    removed: items.slice(cutFrom),
    pivotIdx,
  };
}

export async function requestThreadRewind({
  authToken,
  threadId,
  messageId,
  artifactIds = [],
}) {
  if (!threadId || !messageId) {
    return { skipped: true, reason: "missing_thread_or_message" };
  }
  const r = await fetch(
    `${API_BASE_URL}/api/v1/chat/threads/${threadId}/rewind`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      },
      body: JSON.stringify({
        message_id: messageId,
        artifact_ids: artifactIds,
      }),
    },
  );
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = body?.detail || body?.message || `Rewind failed (${r.status})`;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = r.status;
    throw err;
  }
  return body;
}

/**
 * Orchestrates local timeline truncation + backend message rewind.
 */
export default function useThreadRewind({
  items,
  extras,
  setExtras,
  truncateChatMessages,
  authToken,
  threadId,
  onRefreshArtifacts,
}) {
  const prepareRewindFromUserItem = useCallback(
    async (item) => {
      if (!item || item.kind !== THREAD_KINDS.USER) return null;
      const turnId = item.turnId;
      if (!turnId) return null;

      const { removed } = partitionTimelineAtUserTurn(items, turnId, { dropPivotUser: true });
      const artifactIds = collectArtifactIdsFromItems(removed);
      const extraIdSet = new Set((extras || []).map((e) => e.id));
      const removedExtraIds = new Set(
        removed.filter((it) => extraIdSet.has(it.id)).map((it) => it.id),
      );

      if (setExtras) {
        setExtras((prev) => prev.filter((e) => !removedExtraIds.has(e.id)));
      }

      if (item.backendMessageId && truncateChatMessages) {
        truncateChatMessages(item.backendMessageId);
      }

      if (threadId && item.backendMessageId) {
        try {
          await requestThreadRewind({
            authToken,
            threadId,
            messageId: item.backendMessageId,
            artifactIds,
          });
        } catch (err) {
          // Local truncate already applied; surface for UI if needed.
          err.artifactIds = artifactIds;
          throw err;
        }
      }

      onRefreshArtifacts?.();

      return {
        text: String(item.payload?.text || "").trim(),
        turnId,
        artifactIds,
        hadBackendMessage: Boolean(item.backendMessageId),
      };
    },
    [
      items,
      extras,
      setExtras,
      truncateChatMessages,
      authToken,
      threadId,
      onRefreshArtifacts,
    ],
  );

  return {
    prepareRewindFromUserItem,
    partitionTimelineAtUserTurn,
    collectArtifactIdsFromItems,
  };
}
