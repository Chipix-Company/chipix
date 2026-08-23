/**
 * useOverlayClose — the *one* close pattern every overlay uses.
 *
 * Problem this solves: the back/cancel button was in a different place in
 * every overlay — mid-header in Mental Model, bottom-left in some, middle
 * in others. That's the navigation incoherence the user called out.
 *
 * Contract: every overlay (Mental Model, Dashboard, IDE, Documentation)
 * mounts a `<button>` rendered with the `tf-overlay-close` class in the
 * SAME slot (top-right of the overlay header) and reads its props from
 * here. Esc-to-close is handled centrally so it Just Works.
 *
 * Returns:
 *   {
 *     closeButtonProps,   // spread into the × button
 *     escKeyDown,         // attach to an onKeyDown if the overlay is focused
 *   }
 */

import { useEffect, useCallback } from "react";
import { playTick } from "./useChipixSound";

export default function useOverlayClose({
  open,
  onClose,
  closeOnEsc = true,
  label = "Close",
} = {}) {
  const fire = useCallback(() => {
    playTick({ pitch: "low" });
    onClose?.();
  }, [onClose]);

  // Global Esc handler — bound while open. We listen on `keydown` (capture
  // phase off) so focused inputs that don't `stopPropagation` still close.
  useEffect(() => {
    if (!open || !closeOnEsc) return undefined;
    const handler = (e) => {
      if (e.key !== "Escape") return;
      // Honour preventDefault from descendants — if a modal child wants to
      // claim Esc (eg. a popover inside the overlay), it can call
      // preventDefault on the key event.
      if (e.defaultPrevented) return;
      e.preventDefault();
      fire();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, closeOnEsc, fire]);

  return {
    closeButtonProps: {
      type: "button",
      className: "tf-overlay-close",
      onClick: fire,
      "aria-label": label,
      title: `${label} (Esc)`,
    },
  };
}
