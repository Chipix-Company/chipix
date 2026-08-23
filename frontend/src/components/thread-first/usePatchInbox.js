/**
 * usePatchInbox — polls `listPatches(projectId)` while mounted and exposes a
 * stable {patches, refresh, approve, reject} surface. The thread-first UI
 * consumes this to push DiffCard items into the conversation whenever a new
 * patch shows up. The hook also de-duplicates by patch id so re-emits over
 * polling don't spam the thread.
 *
 * Backend may later push patches over the run WebSocket; the `onNewPatch`
 * callback is the single integration seam where future push events should
 * land — keep this hook the only place that decides "this is a new patch".
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  listPatches as apiListPatches,
  getPatch as apiGetPatch,
  approvePatch as apiApprovePatch,
  rejectPatch as apiRejectPatch,
} from "../../api/verificationApi";

const POLL_MS = 8000;

export default function usePatchInbox({
  projectId,
  authToken,
  enabled = true,
  onNewPatch,
}) {
  const [patches, setPatches] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const seenIdsRef = useRef(new Set());
  /** First successful list for this project: seed seen ids only — do not spam the thread with every historical patch. */
  const bootstrappedRef = useRef(false);
  const onNewPatchRef = useRef(onNewPatch);
  useEffect(() => { onNewPatchRef.current = onNewPatch; }, [onNewPatch]);

  // Reset seen-set whenever the project changes so the next first-load
  // doesn't accidentally re-fire onNewPatch for the previous project.
  useEffect(() => {
    seenIdsRef.current = new Set();
    bootstrappedRef.current = false;
    setPatches([]);
  }, [projectId]);

  const refresh = useCallback(async () => {
    if (!projectId || !enabled) return;
    setLoading(true);
    try {
      const result = await apiListPatches(projectId, authToken);
      const list = Array.isArray(result) ? result : (result?.patches || []);
      setPatches(list);
      setError(null);

      const callback = onNewPatchRef.current;
      const seen = seenIdsRef.current;
      if (!bootstrappedRef.current) {
        for (const p of list) {
          if (p?.id) seen.add(p.id);
        }
        bootstrappedRef.current = true;
      } else if (callback) {
        for (const p of list) {
          if (!p?.id || seen.has(p.id)) continue;
          seen.add(p.id);
          // Fetch the detail (with diff_text) lazily so the thread card has
          // something to render. Errors here are non-fatal — fall back to the
          // list payload.
          try {
            const detail = await apiGetPatch(projectId, p.id, authToken);
            callback({ ...p, ...detail });
          } catch {
            callback(p);
          }
        }
      }
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [projectId, authToken, enabled]);

  useEffect(() => {
    if (!projectId || !enabled) return undefined;
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      await refresh();
    };
    void tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [projectId, enabled, refresh]);

  const approve = useCallback(async (patchId) => {
    if (!projectId || !patchId) return null;
    const res = await apiApprovePatch(projectId, patchId, authToken);
    await refresh();
    return res;
  }, [projectId, authToken, refresh]);

  const reject = useCallback(async (patchId, reason = "Rejected by user") => {
    if (!projectId || !patchId) return null;
    const res = await apiRejectPatch(projectId, patchId, reason, authToken);
    await refresh();
    return res;
  }, [projectId, authToken, refresh]);

  return { patches, loading, error, refresh, approve, reject };
}

/**
 * Parse a unified diff string into the row shape the DiffCard expects.
 * Lives next to the hook so the patch → DiffCard mapping is in one file.
 */
export function diffTextToLines(diffText, limit = 80) {
  if (!diffText) return [];
  const out = [];
  const rows = String(diffText).split("\n");
  let oldLn = 0;
  let newLn = 0;
  for (const row of rows) {
    if (out.length >= limit) {
      out.push({ kind: "ctx", code: `… ${rows.length - limit} more lines …` });
      break;
    }
    if (row.startsWith("@@")) {
      const m = /@@\s*-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s*@@/.exec(row);
      if (m) { oldLn = parseInt(m[1], 10); newLn = parseInt(m[2], 10); }
      out.push({ kind: "ctx", code: row });
      continue;
    }
    if (row.startsWith("+++") || row.startsWith("---") || row.startsWith("diff ") || row.startsWith("index ")) {
      continue;
    }
    if (row.startsWith("+")) {
      out.push({ kind: "add", code: row.slice(1), ln: newLn });
      newLn += 1;
    } else if (row.startsWith("-")) {
      out.push({ kind: "del", code: row.slice(1), ln: oldLn });
      oldLn += 1;
    } else {
      out.push({ kind: "ctx", code: row.replace(/^ /, ""), ln: newLn });
      oldLn += 1; newLn += 1;
    }
  }
  return out;
}

/** Convert a patch payload (list/detail) → a thread "diff" item payload. */
export function patchToDiffPayload(patch) {
  const filename = patch.target_file
    || patch.file_path
    || patch.filename
    || (patch.title || "").replace(/^Fix\s+/i, "").trim()
    || "patch";
  const lines = diffTextToLines(patch.diff_text || patch.diff || "");
  let summary = "";
  const adds = lines.filter((l) => l.kind === "add").length;
  const dels = lines.filter((l) => l.kind === "del").length;
  if (adds || dels) summary = `+${adds} −${dels}`;
  return {
    filename,
    summary,
    lines,
    status: patch.status === "approved" ? "applied" : patch.status === "rejected" ? "rejected" : "pending",
    why: patch.reason || patch.title || "Proposed fix",
    applies_patch_id: patch.id,
  };
}
