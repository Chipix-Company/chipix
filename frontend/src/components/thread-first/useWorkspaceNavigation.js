/**
 * useWorkspaceNavigation — one coherent "where am I?" model for thread-first.
 *
 * Surfaces (mutually exclusive primary destinations):
 *   conversation (base) → dashboard | mental | ide | docs
 *
 * Optional file peek sits above conversation only (quick edit from a card).
 * Opening IDE or any primary surface clears the file peek.
 *
 * Transient layers (palette, drawer, attach modals) are NOT owned here;
 * the workspace Esc handler pops those first, then calls goBack().
 */

import { useCallback, useMemo, useState } from "react";

export const PRIMARY_SURFACES = ["dashboard", "mental", "codebaseGraph", "ide", "docs", "tasks", "founders"];

const LABELS = {
  dashboard: "Dashboard",
  mental: "Mental model",
  codebaseGraph: "Codebase map",
  ide: "Editor",
  docs: "Documentation",
  tasks: "Task board",
  founders: "Letter from the founders",
};

export default function useWorkspaceNavigation() {
  const [primary, setPrimary] = useState(null);
  const [fileTarget, setFileTarget] = useState(null);

  const isOpen = useCallback(
    (id) => {
      if (id === "file") return Boolean(fileTarget);
      return primary === id;
    },
    [primary, fileTarget],
  );

  /** Open a primary surface; closes any other primary + file peek. */
  const openPrimary = useCallback((id) => {
    if (!PRIMARY_SURFACES.includes(id)) return;
    setFileTarget(null);
    setPrimary(id);
  }, []);

  /** Side-rail / repeated shortcut: same control closes when already there. */
  const togglePrimary = useCallback((id) => {
    if (!PRIMARY_SURFACES.includes(id)) return;
    setFileTarget(null);
    setPrimary((prev) => (prev === id ? null : id));
  }, []);

  /** Quick file editor over the conversation (thread cards, palette file pick). */
  const openFile = useCallback((target) => {
    if (!target) return;
    setPrimary(null);
    setFileTarget(target);
  }, []);

  const closeFile = useCallback(() => {
    setFileTarget(null);
  }, []);

  /** Pop one level: file peek → primary surface → conversation. */
  const goBack = useCallback(() => {
    if (fileTarget) {
      setFileTarget(null);
      return true;
    }
    if (primary) {
      setPrimary(null);
      return true;
    }
    return false;
  }, [fileTarget, primary]);

  const closeAll = useCallback(() => {
    setFileTarget(null);
    setPrimary(null);
  }, []);

  const titleBarOverlay = useMemo(() => {
    if (fileTarget) {
      return {
        id: "file",
        label: fileTarget.name || "File",
        sub: fileTarget.memberPath && fileTarget.memberPath !== fileTarget.name
          ? fileTarget.memberPath
          : null,
        onClose: closeFile,
      };
    }
    if (primary) {
      return {
        id: primary,
        label: LABELS[primary] || primary,
        onClose: () => setPrimary(null),
      };
    }
    return null;
  }, [fileTarget, primary, closeFile]);

  const sideRailActive = useMemo(() => {
    if (primary === "mental") return "mental";
    if (primary === "codebaseGraph") return "codebase";
    if (primary === "dashboard") return "dash";
    if (primary === "ide") return "ide";
    if (primary === "tasks") return "tasks";
    if (primary === "founders") return "founders";
    return null;
  }, [primary]);

  return {
    primary,
    fileTarget,
    dashOpen: primary === "dashboard",
    mmOpen: primary === "mental",
    cgOpen: primary === "codebaseGraph",
    ideOpen: primary === "ide",
    docsOpen: primary === "docs",
    tasksOpen: primary === "tasks",
    foundersOpen: primary === "founders",
    editorTarget: fileTarget,
    openPrimary,
    togglePrimary,
    openFile,
    closeFile,
    goBack,
    closeAll,
    isOpen,
    titleBarOverlay,
    sideRailActive,
    setPrimary,
    setFileTarget,
  };
}
