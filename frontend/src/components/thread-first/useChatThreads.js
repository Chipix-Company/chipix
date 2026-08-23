import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import API_BASE_URL from "../../config";
import { sortChatMessagesChronologically } from "./chatMessageOrder";

/**
 * useChatThreads — owns the multi-thread-per-project state machine.
 *
 * Design choices (from first principles):
 *   - One thread per project is the invisible default. New users never
 *     have to think about threads — the hook auto-creates the first one
 *     ("Project Chat") on mount.
 *   - Multi-thread is opt-in. The hook exposes create/switch/rename/delete
 *     primitives; the workspace surfaces them in the palette and drawer,
 *     never as permanent header chrome.
 *   - Active thread id is persisted per-project in localStorage so a reload
 *     drops the user back in the same conversation.
 *   - Messages are hydrated on switch via the cursor-paginated backend
 *     endpoint. Only the first page (newest 50) is fetched eagerly; older
 *     pages can be loaded on demand via loadOlderMessages.
 *   - Title auto-derivation: the consumer calls `maybeAutoTitle(text)`
 *     before sending the first user message. If the thread title is still
 *     the default "Project Chat", the hook PATCHes it to a derived title.
 *     Manual renames win — we never overwrite a user-set title.
 */

const STORAGE_PREFIX = "chipverify.activeChatThread";
const DEFAULT_TITLE = "Project Chat";
const TITLE_MAX = 60;
const MESSAGE_PAGE_SIZE = 50;

function storageKey(projectId) {
  return projectId ? `${STORAGE_PREFIX}:${projectId}` : null;
}

function loadActive(projectId) {
  const k = storageKey(projectId);
  if (!k || typeof window === "undefined") return null;
  try { return window.localStorage.getItem(k) || null; } catch { return null; }
}

function saveActive(projectId, threadId) {
  const k = storageKey(projectId);
  if (!k || typeof window === "undefined") return;
  try {
    if (threadId) window.localStorage.setItem(k, threadId);
    else window.localStorage.removeItem(k);
  } catch {
    // localStorage is best-effort.
  }
}

function authHeaders(authToken) {
  return authToken ? { Authorization: `Bearer ${authToken}` } : {};
}

async function jsonFetch(url, options = {}) {
  const r = await fetch(url, options);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = body?.detail || body?.message || `Request failed (${r.status})`;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = r.status;
    throw err;
  }
  return body;
}

/**
 * Derive a thread title from a user's first message. Pull the first
 * non-empty line, trim and clamp to ~60 chars, strip trailing punctuation.
 */
export function deriveTitleFromMessage(text) {
  if (!text) return DEFAULT_TITLE;
  const firstLine = String(text).split(/\r?\n/).map((l) => l.trim()).find(Boolean) || "";
  const trimmed = firstLine.replace(/[.!?]+$/, "").trim();
  if (!trimmed) return DEFAULT_TITLE;
  if (trimmed.length <= TITLE_MAX) return trimmed;
  return `${trimmed.slice(0, TITLE_MAX - 1).trim()}…`;
}

export default function useChatThreads({ projectId, authToken, enabled = true }) {
  const [threads, setThreads] = useState([]);
  const [activeThreadId, setActiveThreadIdRaw] = useState(null);
  const [chatMessages, setChatMessages] = useState([]);
  const [olderCursor, setOlderCursor] = useState(null);
  const [hasOlderMessages, setHasOlderMessages] = useState(false);

  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState(null);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState(null);

  const bootstrappedRef = useRef(false);
  const messagesFetchTokenRef = useRef(0);
  const projectRef = useRef(projectId);
  // Snapshot of latest threads list so refresh can read without re-rendering.
  const threadsRef = useRef([]);
  useEffect(() => { threadsRef.current = threads; }, [threads]);

  const setActiveThreadId = useCallback((next) => {
    setActiveThreadIdRaw(next);
    saveActive(projectRef.current, next);
  }, []);

  // ─────────────────────────────────────────────────────────────────────
  // Backend interactions
  // ─────────────────────────────────────────────────────────────────────
  const fetchThreads = useCallback(async (pid) => {
    if (!pid) return [];
    setListLoading(true);
    setListError(null);
    try {
      const body = await jsonFetch(
        `${API_BASE_URL}/api/v1/projects/${pid}/chat/threads`,
        { headers: authHeaders(authToken) },
      );
      const list = Array.isArray(body) ? body : [];
      setThreads(list);
      return list;
    } catch (err) {
      setListError(err);
      return [];
    } finally {
      setListLoading(false);
    }
  }, [authToken]);

  const fetchMessages = useCallback(async (threadId) => {
    if (!threadId) { setChatMessages([]); setOlderCursor(null); setHasOlderMessages(false); return; }
    const token = ++messagesFetchTokenRef.current;
    setMessagesLoading(true);
    setMessagesError(null);
    setChatMessages([]);
    try {
      const body = await jsonFetch(
        `${API_BASE_URL}/api/v1/chat/threads/${threadId}/messages?limit=${MESSAGE_PAGE_SIZE}`,
        { headers: authHeaders(authToken) },
      );
      // A more recent switch superseded this fetch — drop the stale result.
      if (token !== messagesFetchTokenRef.current) return;
      const messages = sortChatMessagesChronologically(body.messages);
      setChatMessages(messages);
      setHasOlderMessages(Boolean(body?.pagination?.has_more));
      setOlderCursor(body?.pagination?.next_cursor || null);
    } catch (err) {
      if (token === messagesFetchTokenRef.current) {
        setMessagesError(err);
        setChatMessages([]);
      }
    } finally {
      if (token === messagesFetchTokenRef.current) setMessagesLoading(false);
    }
  }, [authToken]);

  const loadOlderMessages = useCallback(async () => {
    if (!activeThreadId || !olderCursor) return;
    setMessagesLoading(true);
    try {
      const body = await jsonFetch(
        `${API_BASE_URL}/api/v1/chat/threads/${activeThreadId}/messages?limit=${MESSAGE_PAGE_SIZE}&before_message_id=${encodeURIComponent(olderCursor)}`,
        { headers: authHeaders(authToken) },
      );
      const older = sortChatMessagesChronologically(body.messages);
      setChatMessages((prev) => [...older, ...prev]);
      setHasOlderMessages(Boolean(body?.pagination?.has_more));
      setOlderCursor(body?.pagination?.next_cursor || null);
    } catch (err) {
      setMessagesError(err);
    } finally {
      setMessagesLoading(false);
    }
  }, [authToken, activeThreadId, olderCursor]);

  const refreshMessages = useCallback(async () => {
    if (!activeThreadId) return;
    await fetchMessages(activeThreadId);
  }, [activeThreadId, fetchMessages]);

  const createThread = useCallback(async ({ title, switchTo = true } = {}) => {
    if (!projectId) return null;
    const body = await jsonFetch(
      `${API_BASE_URL}/api/v1/projects/${projectId}/chat/threads`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
        body: JSON.stringify({ title: (title || DEFAULT_TITLE).trim() || DEFAULT_TITLE }),
      },
    );
    setThreads((prev) => [body, ...prev]);
    if (switchTo) {
      setActiveThreadId(body.id);
      setChatMessages([]);
      setOlderCursor(null);
      setHasOlderMessages(false);
    }
    return body;
  }, [projectId, authToken, setActiveThreadId]);

  const switchThread = useCallback(async (threadId) => {
    if (!threadId || threadId === activeThreadId) return;
    setActiveThreadId(threadId);
    await fetchMessages(threadId);
  }, [activeThreadId, fetchMessages, setActiveThreadId]);

  // Optimistic local-only title update — used when the backend tells us
  // it generated a title (via the agent WS "thread_title_generated" event)
  // and we don't want to re-PATCH (it just came from the server).
  const applyLocalTitle = useCallback((threadId, title) => {
    if (!threadId || !title) return;
    setThreads((prev) => prev.map((t) => (t.id === threadId ? { ...t, title } : t)));
  }, []);

  const renameThread = useCallback(async (threadId, title) => {
    if (!threadId || !title?.trim()) return;
    const body = await jsonFetch(
      `${API_BASE_URL}/api/v1/chat/threads/${threadId}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
        body: JSON.stringify({ title: title.trim() }),
      },
    );
    setThreads((prev) => prev.map((t) => (t.id === threadId ? { ...t, ...body } : t)));
    return body;
  }, [authToken]);

  const archiveThread = useCallback(async (threadId, archived = true) => {
    if (!threadId) return;
    const body = await jsonFetch(
      `${API_BASE_URL}/api/v1/chat/threads/${threadId}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
        body: JSON.stringify({ archived }),
      },
    );
    setThreads((prev) => prev.map((t) => (t.id === threadId ? { ...t, ...body } : t)));
    // If the user archived the active thread, fall back to the most recent
    // non-archived thread (or create one if none remain).
    if (archived && threadId === activeThreadId) {
      const remaining = threadsRef.current.filter((t) => t.id !== threadId && !t.archived);
      if (remaining.length) {
        await switchThread(remaining[0].id);
      } else {
        const fresh = await createThread({ title: DEFAULT_TITLE });
        if (fresh) await switchThread(fresh.id);
      }
    }
    return body;
  }, [authToken, activeThreadId, switchThread, createThread]);

  const deleteThread = useCallback(async (threadId) => {
    if (!threadId) return;
    await jsonFetch(
      `${API_BASE_URL}/api/v1/chat/threads/${threadId}`,
      { method: "DELETE", headers: authHeaders(authToken) },
    );
    setThreads((prev) => prev.filter((t) => t.id !== threadId));
    if (threadId === activeThreadId) {
      const remaining = threadsRef.current.filter((t) => t.id !== threadId);
      if (remaining.length) {
        await switchThread(remaining[0].id);
      } else {
        const fresh = await createThread({ title: DEFAULT_TITLE });
        if (fresh) await switchThread(fresh.id);
      }
    }
  }, [authToken, activeThreadId, switchThread, createThread]);

  // Auto-title: called by the workspace right before sending the first
  // user message of an as-yet-unnamed thread. We only rename if the title
  // is still the default — a manual rename always wins.
  const truncateMessagesAfter = useCallback((messageId) => {
    if (!messageId) return;
    setChatMessages((prev) => {
      const idx = prev.findIndex((m) => m.id === messageId);
      if (idx < 0) return prev;
      return prev.slice(0, idx);
    });
  }, []);

  const maybeAutoTitle = useCallback(async (text) => {
    if (!activeThreadId || !text) return;
    const current = threadsRef.current.find((t) => t.id === activeThreadId);
    if (!current) return;
    if ((current.title || "").trim() !== DEFAULT_TITLE) return;
    const next = deriveTitleFromMessage(text);
    if (!next || next === DEFAULT_TITLE) return;
    try {
      await renameThread(activeThreadId, next);
    } catch {
      // Auto-title failures are non-fatal — the thread still works.
    }
  }, [activeThreadId, renameThread]);

  // ─────────────────────────────────────────────────────────────────────
  // Bootstrap on project change
  // ─────────────────────────────────────────────────────────────────────
  useEffect(() => {
    projectRef.current = projectId;
    bootstrappedRef.current = false;
    setThreads([]);
    setActiveThreadIdRaw(null);
    setChatMessages([]);
    setOlderCursor(null);
    setHasOlderMessages(false);
    if (!projectId || !enabled) return;

    let cancelled = false;
    (async () => {
      const list = await fetchThreads(projectId);
      if (cancelled) return;
      const stored = loadActive(projectId);
      let target = null;
      if (stored && list.some((t) => t.id === stored)) {
        target = stored;
      } else if (list.length) {
        target = list[0].id;
      }
      if (!target) {
        // Empty project — create the default thread so the workspace always
        // has a non-null threadId to stream against.
        const fresh = await createThread({ title: DEFAULT_TITLE, switchTo: false });
        if (cancelled || !fresh) return;
        target = fresh.id;
      }
      if (cancelled) return;
      setActiveThreadId(target);
      await fetchMessages(target);
      bootstrappedRef.current = true;
    })();

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, enabled]);

  // ─────────────────────────────────────────────────────────────────────
  // Derived
  // ─────────────────────────────────────────────────────────────────────
  const activeThread = useMemo(
    () => threads.find((t) => t.id === activeThreadId) || null,
    [threads, activeThreadId],
  );

  const refreshThreads = useCallback(
    () => fetchThreads(projectRef.current),
    [fetchThreads],
  );

  return {
    threads,
    activeThread,
    activeThreadId,
    chatMessages,
    listLoading,
    listError,
    messagesLoading,
    messagesError,
    hasOlderMessages,
    loadOlderMessages,
    refreshMessages,
    createThread,
    switchThread,
    renameThread,
    archiveThread,
    deleteThread,
    maybeAutoTitle,
    applyLocalTitle,
    truncateMessagesAfter,
    refreshThreads,
  };
}
