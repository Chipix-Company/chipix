import React, { useEffect, useRef } from "react";
import Icon from "../icons";

/**
 * Small floating panel showing code while the agent writes a file.
 * Anchored above the composer status chip; dismissible; reopen via status click.
 */
export default function LiveWritePeek({
  open,
  liveWrite,
  onClose,
}) {
  const preRef = useRef(null);

  useEffect(() => {
    const el = preRef.current;
    if (!el || !open) return;
    el.scrollTop = el.scrollHeight;
  }, [liveWrite?.content, open]);

  if (!open || !liveWrite?.content) return null;

  const { filename, content, language, phase } = liveWrite;
  const lineCount = content ? content.split(/\r?\n/).length : 0;
  const phaseLabel = phase === "done" ? "Saved" : phase === "streaming" ? "Generating…" : "Writing…";

  return (
    <div className="tf-live-write-peek" role="complementary" aria-label={`Live preview: ${filename || "file"}`}>
      <div className="tf-live-write-head">
        <div className="tf-live-write-title">
          <span className="tf-live-write-phase">{phaseLabel}</span>
          <span className="tf-live-write-name">{filename || "RTL"}</span>
          {language ? <span className="tf-live-write-lang">{language}</span> : null}
        </div>
        <button
          type="button"
          className="tf-live-write-close"
          onClick={() => onClose?.()}
          aria-label="Hide preview"
          title="Hide preview"
        >
          <Icon.X width="14" height="14" />
        </button>
      </div>
      <pre ref={preRef} className="tf-live-write-code">
        <code>{content}</code>
      </pre>
      <div className="tf-live-write-foot">
        <span>{lineCount} line{lineCount === 1 ? "" : "s"}</span>
        <span className="tf-live-write-hint">Updates while the agent works</span>
      </div>
    </div>
  );
}
