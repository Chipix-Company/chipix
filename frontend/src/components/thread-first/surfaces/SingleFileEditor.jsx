import React, { useEffect, useRef, useState } from "react";
import Icon from "../icons";
import ArtifactDocumentPane from "./editor/ArtifactDocumentPane";
import { fileGlyph, getEditorFileKind, inferMonacoLanguage } from "./editor/ideEditorUtils";
import { formatEditorContent } from "./editor/formatEditorContent";

/**
 * SingleFileEditor — full-viewport overlay focused on one file.
 * Loads content via onLoad({ artifactId, memberPath }), saves via onSave({ ..., content }).
 */
export default function SingleFileEditor({
  open,
  onClose,
  target,
  onLoad,
  onLoadBlob,
  onSave,
  onAsk,
  isDarkTheme = true,
}) {
  const [content, setContent] = useState("");
  const [original, setOriginal] = useState("");
  const [pdfBlob, setPdfBlob] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [error, setError] = useState("");
  const [ask, setAsk] = useState("");
  const editorRef = useRef(null);

  const isPdf = getEditorFileKind(target?.name) === "pdf";

  useEffect(() => {
    if (!open || !target) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    setPdfBlob(null);

    const run = async () => {
      try {
        if (isPdf && onLoadBlob) {
          const [blob, text] = await Promise.all([
            onLoadBlob(target),
            onLoad?.(target),
          ]);
          if (cancelled) return;
          setPdfBlob(blob || null);
          const value = typeof text === "string" ? text : "";
          setContent(value);
          setOriginal(value);
          return;
        }
        const text = await onLoad?.(target);
        if (cancelled) return;
        const rawValue = typeof text === "string" ? text : "";
        const value = await formatEditorContent(rawValue, target?.name || "");
        if (cancelled) return;
        setContent(value);
        setOriginal(value);
      } catch (err) {
        if (cancelled) return;
        setError(err?.message || "Could not load file.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void run();
    return () => { cancelled = true; };
  }, [open, target, onLoad, onLoadBlob, isPdf]);

  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void handleSave();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, content, isPdf]);

  useEffect(() => {
    if (!editorRef.current || !target?.focusLine || isPdf) return;
    try {
      editorRef.current.revealLineInCenter(target.focusLine);
      editorRef.current.setPosition({ lineNumber: target.focusLine, column: 1 });
    } catch { /* noop */ }
  }, [target?.focusLine, loading, isPdf]);

  const handleSave = async () => {
    if (!target || saving || isPdf) return;
    if (content === original) return;
    setSaving(true);
    try {
      await onSave?.({ ...target, content });
      setOriginal(content);
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 1400);
    } catch (err) {
      setError(err?.message || "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  const dirty = !isPdf && content !== original;
  const lang = target?.language || inferMonacoLanguage(target?.name || "");
  const fileModel = {
    name: target?.name || "untitled",
    kind: getEditorFileKind(target?.name),
    language: lang,
    content,
    pdfBlob,
    loaded: !loading,
  };

  return (
    <div className="tf-editor open" role="dialog" aria-label="File editor">
      <header className="tf-editor-h">
        <div className="tf-editor-title">
          <div className="glyph">{fileGlyph(target?.name)}</div>
          <div>
            <div className="name">{target?.name || "untitled"}</div>
            <div className="meta">
              {target?.memberPath ? `member: ${target.memberPath}` : `artifact: ${target?.artifactId || "—"}`}
              {target?.focusLine ? ` · line ${target.focusLine}` : ""}
              {isPdf ? " · PDF preview" : ""}
            </div>
          </div>
        </div>
        <span className="spacer" />
        <span className={`saved ${savedFlash ? "show" : ""}`}>Saved</span>
        {error ? <span className="tf-run-status bad">{error}</span> : null}
        {!isPdf ? (
          <button className="tf-btn sm" onClick={handleSave} disabled={!dirty || saving}>
            {saving ? "Saving…" : dirty ? "Save (⌘S)" : "Saved"}
          </button>
        ) : null}
        <button
          type="button"
          className="tf-overlay-close"
          onClick={() => onClose?.()}
          aria-label="Close file"
          title="Close file (Esc)"
        >
          <Icon.Close width="14" height="14" />
        </button>
      </header>

      <div className="tf-editor-body-wrap">
        {loading ? (
          <div className="tf-ide-editor-loading">Loading file…</div>
        ) : (
          <ArtifactDocumentPane
            file={fileModel}
            isDarkTheme={isDarkTheme}
            readOnly={isPdf}
            onChange={setContent}
            onMount={(editor) => {
              editorRef.current = editor;
              if (target?.focusLine) {
                try {
                  editor.revealLineInCenter(target.focusLine);
                  editor.setPosition({ lineNumber: target.focusLine, column: 1 });
                } catch { /* noop */ }
              }
            }}
          />
        )}
      </div>

      <footer className="tf-editor-footer">
        <div className="inner">
          <div className="prompt">
            <span className="lbl">Ask Chipix</span>
            <input
              placeholder={`e.g. "Explain this module" or "Make this synthesizable for FPGA"`}
              value={ask}
              onChange={(e) => setAsk(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && ask.trim()) {
                  const text = ask.trim();
                  setAsk("");
                  onAsk?.(text);
                  onClose?.();
                }
              }}
            />
            <button
              type="button"
              className="tf-btn sm primary"
              onClick={() => {
                if (!ask.trim()) return;
                const text = ask.trim();
                setAsk("");
                onAsk?.(text);
                onClose?.();
              }}
              disabled={!ask.trim()}
              aria-label="Ask"
            >
              <Icon.Send width="12" height="12" />
            </button>
          </div>
          {onAsk ? (
            <div className="suggests">
              {[
                "Explain this file",
                "Find issues",
                "Make this synthesizable for FPGA",
              ].map((q) => (
                <button
                  key={q}
                  type="button"
                  className="sg2"
                  onClick={() => {
                    onAsk?.(q);
                    onClose?.();
                  }}
                >
                  {q}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </footer>
    </div>
  );
}
