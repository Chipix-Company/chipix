import React, { useEffect } from "react";
import { createPortal } from "react-dom";

/**
 * VS Code–style fixed context menu for the IDE file explorer.
 */
export default function IdeExplorerContextMenu({
  open,
  x,
  y,
  file,
  busy = false,
  onClose,
  onRename,
  onDelete,
  onUpload,
}) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const canRename = Boolean(file) && !file.memberPath;
  const canDelete = Boolean(file) && !file.memberPath;

  return createPortal(
    <>
      <button
        type="button"
        className="tf-ide-ctx-backdrop"
        aria-label="Close menu"
        onClick={() => onClose?.()}
      />
      <div
        className="tf-ide-ctx-menu"
        style={{ left: x, top: y }}
        role="menu"
        onContextMenu={(e) => e.preventDefault()}
      >
        {canRename ? (
          <button
            type="button"
            role="menuitem"
            disabled={busy}
            onClick={() => {
              onRename?.(file);
              onClose?.();
            }}
          >
            Rename…
          </button>
        ) : null}
        {canDelete ? (
          <button
            type="button"
            role="menuitem"
            className="danger"
            disabled={busy}
            onClick={() => {
              onDelete?.(file);
              onClose?.();
            }}
          >
            Delete
          </button>
        ) : null}
        {canRename || canDelete ? <div className="tf-ide-ctx-sep" role="separator" /> : null}
        <button
          type="button"
          role="menuitem"
          disabled={busy}
          onClick={() => {
            onUpload?.();
            onClose?.();
          }}
        >
          Upload file(s)…
        </button>
      </div>
    </>,
    document.body,
  );
}
