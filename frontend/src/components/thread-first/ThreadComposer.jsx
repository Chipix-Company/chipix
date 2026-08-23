import React, { useRef, useEffect, useState, useCallback } from "react";
import Icon from "./icons";
import LiveWritePeek from "./surfaces/LiveWritePeek";
import ContextUsageMeter from "./ContextUsageMeter";

function formatElapsed(ms) {
  if (ms == null) return "";
  const s = Math.max(0, Math.floor(ms / 1000));
  const mm = Math.floor(s / 60);
  const ss = s % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

export default function ThreadComposer({
  value,
  onChange,
  onSend,
  onUploadSpec,
  onUploadRtl,
  onUploadRtlFolder,
  onOpenWorkspaceAttach,
  attachmentChips = [],
  onRemoveAttachment,
  attachStatusLine = "",
  suggestions = [],
  onSuggestion,
  disabled,
  busy = false,
  onCancel,
  currentFile = null,
  liveWrite = null,
  streamStartedAt = null,
  placeholder = "Describe what you want to build, or ask anything…",
  showHdlLanguagePicker = false,
  hdlLanguage = "systemverilog",
  onHdlLanguageChange,
  modeRail = null,
  tokenContext = null,
}) {
  const taRef = useRef(null);
  const attachWrapRef = useRef(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [recentlyCompleted, setRecentlyCompleted] = useState(null);
  const prevBusyRef = useRef(false);
  const [peekOpen, setPeekOpen] = useState(false);
  const lastPeekFileRef = useRef(null);
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);

  const hasLiveCode = Boolean(liveWrite?.content);
  const canPeek = hasLiveCode && (busy || liveWrite?.phase === "done");

  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(200, el.scrollHeight)}px`;
  }, [value]);

  useEffect(() => {
    if (!busy || !streamStartedAt) {
      setElapsedMs(0);
      return undefined;
    }
    const tick = () => setElapsedMs(Date.now() - streamStartedAt);
    tick();
    const id = setInterval(tick, 250);
    return () => clearInterval(id);
  }, [busy, streamStartedAt]);

  useEffect(() => {
    const prev = prevBusyRef.current;
    prevBusyRef.current = busy;
    if (prev && !busy && streamStartedAt) {
      const finalElapsed = Date.now() - streamStartedAt;
      setRecentlyCompleted({ ms: finalElapsed });
      const id = setTimeout(() => setRecentlyCompleted(null), 4200);
      return () => clearTimeout(id);
    }
    return undefined;
  }, [busy, streamStartedAt]);

  useEffect(() => {
    const key = liveWrite?.filename || (liveWrite?.content ? "__content__" : null);
    if (!key || !hasLiveCode) return;
    if (lastPeekFileRef.current !== key) {
      lastPeekFileRef.current = key;
      setPeekOpen(true);
    }
  }, [liveWrite?.filename, liveWrite?.content, hasLiveCode]);

  useEffect(() => {
    if (!busy && liveWrite?.phase !== "writing" && liveWrite?.phase !== "streaming") {
      lastPeekFileRef.current = null;
    }
  }, [busy, liveWrite?.phase]);

  useEffect(() => {
    if (!attachMenuOpen) return;
    const fn = (e) => {
      if (!attachWrapRef.current?.contains(e.target)) setAttachMenuOpen(false);
    };
    document.addEventListener("mousedown", fn);
    return () => document.removeEventListener("mousedown", fn);
  }, [attachMenuOpen]);

  const handleKey = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      if ((value?.trim() || attachmentChips.some((c) => c.status === "ready" && c.kind === "context")) && !disabled && !busy) onSend?.();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if ((value?.trim() || attachmentChips.some((c) => c.status === "ready" && c.kind === "context")) && !disabled && !busy) onSend?.();
    }
  };

  const hasContextAttach = attachmentChips.some((c) => c.status === "ready" && c.kind === "context");
  const canSend = Boolean(value?.trim() || hasContextAttach);
  const showProjectUploads = Boolean(onUploadSpec || onUploadRtl || onUploadRtlFolder);

  const togglePeek = useCallback(() => {
    if (!canPeek) return;
    setPeekOpen((v) => !v);
  }, [canPeek]);

  const showStatusLine = busy || recentlyCompleted;
  const statusText = busy
    ? currentFile
      ? `Writing ${currentFile} · ${formatElapsed(elapsedMs)}`
      : `Working · ${formatElapsed(elapsedMs)}`
    : recentlyCompleted
      ? `Done · ${formatElapsed(recentlyCompleted.ms)}`
      : "";

  return (
    <div className="tf-composer-wrap" data-tour="composer">
      <div className="tf-composer-inner">
        <LiveWritePeek
          open={peekOpen && canPeek}
          liveWrite={liveWrite}
          onClose={() => setPeekOpen(false)}
        />

        {showStatusLine ? (
          <button
            type="button"
            className={`tf-composer-status${busy ? " busy" : " done"}${canPeek ? " peekable" : ""}`}
            role="status"
            aria-live="polite"
            disabled={!canPeek}
            onClick={canPeek ? togglePeek : undefined}
            title={
              canPeek
                ? peekOpen
                  ? "Hide live code preview"
                  : "Show live code preview"
                : undefined
            }
          >
            {busy ? <span className="tf-composer-status-dot" aria-hidden /> : (
              <span className="tf-composer-status-check" aria-hidden>
                <Icon.Check width="11" height="11" />
              </span>
            )}
            <span className="tf-composer-status-text">{statusText}</span>
            {canPeek ? (
              <span className="tf-composer-status-peek" aria-hidden>
                {peekOpen ? "▾" : "▸"} code
              </span>
            ) : null}
          </button>
        ) : null}

        {(modeRail || (showHdlLanguagePicker && !busy) || (suggestions.length > 0 && !busy)) ? (
          <div className="tf-composer-toolbar">
            <div className="tf-composer-toolbar-main">
              {showHdlLanguagePicker && !busy ? (
                <div className="tf-hdl-lang" role="group" aria-label="RTL language for design">
                  <span className="tf-hdl-lang-lbl">RTL language</span>
                  <button
                    type="button"
                    className={`tf-chip${hdlLanguage === "systemverilog" ? " active" : ""}`}
                    aria-pressed={hdlLanguage === "systemverilog"}
                    onClick={() => onHdlLanguageChange?.("systemverilog")}
                  >
                    SystemVerilog
                  </button>
                  <button
                    type="button"
                    className={`tf-chip${hdlLanguage === "verilog" ? " active" : ""}`}
                    aria-pressed={hdlLanguage === "verilog"}
                    onClick={() => onHdlLanguageChange?.("verilog")}
                  >
                    Verilog
                  </button>
                </div>
              ) : null}

              {suggestions.length && !busy ? (
                <div className="tf-suggest" role="toolbar" aria-label="Suggested actions">
                  {suggestions.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      className={`tf-sgg ${s.accent ? "accent" : ""}`}
                      onClick={() => onSuggestion?.(s)}
                    >
                      {s.label}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            {modeRail}
          </div>
        ) : null}

        {(attachmentChips.length > 0 || attachStatusLine) && !busy ? (
          <div className="tf-attach-strip" aria-label="Attached files">
            {attachStatusLine ? (
              <p className="tf-attach-strip-status" role="status">{attachStatusLine}</p>
            ) : null}
            {attachmentChips.length > 0 ? (
              <div className="tf-pending-attach">
                {attachmentChips.map((a) => (
                  <div
                    key={a.key}
                    className={`tf-pending-chip kind-${a.kind || "context"} status-${a.status || "ready"}`}
                    title={a.error || a.label}
                  >
                    {a.status === "uploading" ? (
                      <span className="tf-pending-chip-spinner" aria-hidden />
                    ) : a.status === "ready" ? (
                      <span className="tf-pending-chip-mark" aria-hidden>✓</span>
                    ) : a.status === "error" ? (
                      <span className="tf-pending-chip-mark err" aria-hidden>!</span>
                    ) : a.status === "missing" ? (
                      <span className="tf-pending-chip-mark warn" aria-hidden>○</span>
                    ) : null}
                    <span className="tf-pending-chip-kind">{a.kindLabel || a.kind}</span>
                    <span className="tf-pending-chip-name">{a.label}</span>
                    {a.removable ? (
                      <button
                        type="button"
                        aria-label={`Remove ${a.label}`}
                        onClick={() => onRemoveAttachment?.(a.key)}
                      >
                        ×
                      </button>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

        <div className={`tf-composer${busy ? " busy" : ""}`}>
          <div className="tf-attach-wrap" ref={attachWrapRef}>
            <button
              type="button"
              className="tool"
              title="Attach files"
              disabled={busy}
              aria-expanded={attachMenuOpen}
              aria-haspopup="menu"
              onClick={() => setAttachMenuOpen((v) => !v)}
            >
              <Icon.Paperclip width="18" height="18" />
            </button>
            {attachMenuOpen && !busy ? (
              <div className="tf-attach-menu" role="menu">
                {showProjectUploads ? (
                  <>
                    <div className="tf-attach-menu-label">Add to project</div>
                    {onUploadSpec ? (
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setAttachMenuOpen(false);
                          onUploadSpec();
                        }}
                      >
                        Upload specification…
                      </button>
                    ) : null}
                    {onUploadRtl ? (
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setAttachMenuOpen(false);
                          onUploadRtl();
                        }}
                      >
                        Upload RTL file(s)…
                      </button>
                    ) : null}
                    {onUploadRtlFolder ? (
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setAttachMenuOpen(false);
                          onUploadRtlFolder();
                        }}
                      >
                        Upload RTL folder…
                      </button>
                    ) : null}
                    {onOpenWorkspaceAttach ? (
                      <div className="tf-attach-menu-divider" role="separator" />
                    ) : null}
                  </>
                ) : null}
                {onOpenWorkspaceAttach ? (
                  <>
                    {showProjectUploads ? (
                      <div className="tf-attach-menu-label">Message context</div>
                    ) : null}
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setAttachMenuOpen(false);
                        onOpenWorkspaceAttach();
                      }}
                    >
                      {showProjectUploads ? "Attach from workspace…" : "Pick from project…"}
                    </button>
                  </>
                ) : null}
              </div>
            ) : null}
          </div>
          <textarea
            id="thread-composer-input"
            name="thread-composer-input"
            aria-label="Describe what you want to build, or ask anything"
            ref={taRef}
            value={value}
            onChange={(e) => onChange?.(e.target.value)}
            onKeyDown={handleKey}
            placeholder={busy ? "Generating — press Cancel to stop" : placeholder}
            rows={1}
            disabled={busy}
          />
          <div className="tf-composer-trailing">
            <ContextUsageMeter tokenContext={tokenContext} />
            {busy ? (
              <button
                className="send cancel"
                onClick={() => onCancel?.()}
                aria-label="Cancel"
                title="Cancel"
              >
                <Icon.X width="14" height="14" />
              </button>
            ) : (
              <button
                className="send"
                onClick={() => canSend && !disabled && !busy && onSend?.()}
                disabled={!canSend || disabled}
                aria-label="Send"
              >
                <Icon.Send width="16" height="16" />
              </button>
            )}
          </div>
        </div>
        <div className="tf-composer-foot">
          <span className="tf-composer-hints">
            <span className="tf-kbd">⌘ Enter</span> send · <span className="tf-kbd">⌘ K</span> palette ·{" "}
            <span className="tf-kbd">⌘ ⇧ E</span> IDE mode
          </span>
        </div>
      </div>
    </div>
  );
}
