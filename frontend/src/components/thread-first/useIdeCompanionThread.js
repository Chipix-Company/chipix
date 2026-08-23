import { useCallback, useEffect, useRef, useState } from "react";
import API_BASE_URL from "../../config";
import { sortChatMessagesChronologically } from "./chatMessageOrder";

function authHeaders(authToken) {
  return authToken ? { Authorization: `Bearer ${authToken}` } : {};
}

async function jsonFetch(url, options = {}) {
  const r = await fetch(url, options);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = body?.detail || body?.message || `Request failed (${r.status})`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

function mapApiMessages(messages) {
  return sortChatMessagesChronologically(messages)
    .filter((m) => m && (m.role === "user" || m.role === "assistant"))
    .map((m) => ({
      role: m.role === "user" ? "user" : "assistant",
      text: String(m.content || "").trim(),
      tokenUsage: m.token_usage || null,
    }))
    .filter((m) => m.text);
}

/**
 * One persisted IDE chat thread per main workspace thread (stored in DB).
 * Messages here never appear in the main conversation timeline.
 */
export default function useIdeCompanionThread({
  projectId,
  mainThreadId,
  authToken,
  enabled = false,
}) {
  const [ideThreadId, setIdeThreadId] = useState(null);
  const [ideMessages, setIdeMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const fetchTokenRef = useRef(0);

  const fetchIdeMessages = useCallback(async (threadId) => {
    if (!threadId) {
      setIdeMessages([]);
      return;
    }
    const token = ++fetchTokenRef.current;
    try {
      const body = await jsonFetch(
        `${API_BASE_URL}/api/v1/chat/threads/${threadId}/messages?limit=50`,
        { headers: authHeaders(authToken) },
      );
      if (token !== fetchTokenRef.current) return;
      setIdeMessages(mapApiMessages(body.messages));
    } catch (err) {
      if (token === fetchTokenRef.current) setError(err);
    }
  }, [authToken]);

  const ensureCompanion = useCallback(async () => {
    if (!projectId || !mainThreadId) return null;
    const body = await jsonFetch(
      `${API_BASE_URL}/api/v1/projects/${projectId}/chat/threads/${mainThreadId}/ide-companion`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
        body: JSON.stringify({}),
      },
    );
    const tid = body?.thread?.id;
    if (!tid) throw new Error("IDE companion thread was not created");
    setIdeThreadId(tid);
    await fetchIdeMessages(tid);
    return tid;
  }, [projectId, mainThreadId, authToken, fetchIdeMessages]);

  useEffect(() => {
    if (!enabled || !projectId || !mainThreadId) {
      setIdeThreadId(null);
      setIdeMessages([]);
      setError(null);
      return undefined;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    ensureCompanion()
      .catch((err) => {
        if (!cancelled) setError(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [enabled, projectId, mainThreadId, ensureCompanion]);

  const refreshIdeMessages = useCallback(async () => {
    if (ideThreadId) await fetchIdeMessages(ideThreadId);
  }, [ideThreadId, fetchIdeMessages]);

  return {
    ideThreadId,
    ideMessages,
    ideLoading: loading,
    ideError: error,
    ensureCompanion,
    refreshIdeMessages,
    setIdeMessages,
  };
}
