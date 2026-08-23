import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Icon from "../icons";
import RtlVisualizer from "./RtlVisualizer";
import ArtifactDocumentPane from "./editor/ArtifactDocumentPane";
import { getEditorFileKind, isInlineCompletionEnabled, isRtlFile, setInlineCompletionEnabled } from "./editor/ideEditorUtils";
import { formatCompletionDebugLine } from "./editor/ideInlineCompletion/CompletionDebug";
import { playTick, pulseHaptic } from "../useChipixSound";
import useOverlayClose from "../useOverlayClose";
import IdeExplorerContextMenu from "./IdeExplorerContextMenu";
import TfMarkdown from "../TfMarkdown";
import ContextUsageMeter from "../ContextUsageMeter";

const ARTIFACT_DRAG_MIME = "application/x-chipix-artifact";

function parseArtifactDrag(dataTransfer) {
  if (!dataTransfer) return null;
  const raw = dataTransfer.getData(ARTIFACT_DRAG_MIME);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed?.artifactId) return null;
    return {
      artifactId: parsed.artifactId,
      label: parsed.label || parsed.name || "file",
      memberPath: parsed.memberPath || undefined,
    };
  } catch {
    return null;
  }
}

const BOTTOM_TABS = [
  { id: "output", label: "Output", icon: "Terminal" },
  { id: "telemetry", label: "Telemetry", icon: "Activity" },
  { id: "diff", label: "Diff", icon: "Code" },
  { id: "tests", label: "Tests", icon: "Beaker" },
];

function fmtTs(ts) {
  if (!ts) return "";
  try {
    const d = typeof ts === "number" ? new Date(ts) : new Date(ts);
    return d.toLocaleTimeString();
  } catch {
    return "";
  }
}

function estimateTokensFromText(text) {
  return Math.max(0, Math.ceil(String(text || "").length / 4));
}

function formatTokenCount(value) {
  const n = Number(value || 0);
  if (n >= 1000000) return `${(n / 1000000).toFixed(n >= 10000000 ? 0 : 1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K`;
  return String(Math.max(0, Math.round(n)));
}

function totalTokensFromUsage(usage) {
  if (!usage) return 0;
  return Number(usage.total_tokens ?? usage.totalTokens ?? 0) || 0;
}

function TelemetryPane({ events }) {
  if (!events?.length) {
    return <div className="tf-ide-pane-empty">No telemetry yet. Live agent events stream here while a run is in progress.</div>;
  }
  return (
    <div className="tf-ide-telemetry">
      {events.slice(-200).map((e, i) => {
        const level = String(e.level || "info").toLowerCase();
        return (
          <div key={i} className={`row ${level}`}>
            <span className="ts">{fmtTs(e.ts || e.created_at || e.timestamp)}</span>
            {e.phase ? <span className="phase">{e.phase}</span> : null}
            {e.agent ? <span className="agent">{e.agent}</span> : null}
            <span className="msg">{e.message || e.text || ""}</span>
          </div>
        );
      })}
    </div>
  );
}

function DiffPane({ recentDiff, onOpenInThread }) {
  if (!recentDiff) {
    return <div className="tf-ide-pane-empty">No pending diff. Proposed patches appear in the conversation as diff cards; this pane mirrors the most recent one.</div>;
  }
  const lines = recentDiff.lines || [];
  return (
    <div className="tf-ide-diffpane">
      <div className="hdr">
        <span className="t">{recentDiff.title || "Proposed change"}</span>
        {recentDiff.summary ? <span className="s">{recentDiff.summary}</span> : null}
        <span className="spacer" />
        {onOpenInThread ? (
          <button className="tf-btn sm" onClick={onOpenInThread}>Open in thread</button>
        ) : null}
      </div>
      <div className="body">
        {lines.length === 0 ? (
          <div className="muted">Diff body not available — open in thread to review.</div>
        ) : (
          lines.map((l, i) => (
            <div key={i} className={`ln ${l.type || l.kind || ""}`}>
              <span className="g">{(l.type || l.kind) === "add" ? "+" : (l.type || l.kind) === "del" ? "−" : " "}</span>
              <span className="c">{l.code}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function TestsPane({ runs, onOpenRun }) {
  const [sort, setSort] = useState("recent");
  const sorted = useMemo(() => {
    const list = Array.isArray(runs) ? runs.slice() : [];
    if (sort === "status") {
      list.sort((a, b) => String(a.status || "").localeCompare(String(b.status || "")));
    } else {
      list.sort((a, b) => String(b.created_at || b.started_at || "").localeCompare(String(a.created_at || a.started_at || "")));
    }
    return list;
  }, [runs, sort]);

  if (!sorted.length) {
    return <div className="tf-ide-pane-empty">No verification runs yet. Once you start a run, every test outcome will sort and pivot here.</div>;
  }
  return (
    <div className="tf-ide-tests">
      <div className="bar">
        <span className="lbl">Sort</span>
        <button className={`chip ${sort === "recent" ? "on" : ""}`} onClick={() => setSort("recent")}>Recent</button>
        <button className={`chip ${sort === "status" ? "on" : ""}`} onClick={() => setSort("status")}>Status</button>
      </div>
      <div className="rows">
        {sorted.slice(0, 40).map((r) => {
          const status = String(r.status || "").toLowerCase();
          const tone = /pass|complete/.test(status) ? "good" : /fail|error/.test(status) ? "bad" : /run/.test(status) ? "busy" : "idle";
          return (
            <button key={r.id || r.run_id} className={`row ${tone}`} onClick={() => onOpenRun?.(r)}>
              <span className="dot" />
              <span className="title">{r.title || r.summary || `Run ${String(r.id || r.run_id || "").slice(0, 8)}`}</span>
              <span className="meta">{status || "—"}</span>
              <span className="when">{fmtTs(r.created_at || r.started_at)}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * IdeWorkspace — code + live telemetry. Nothing else.
 *
 *   ┌─ Header ──────────────────────────────────────────────────────────┐
 *   ├─ Explorer (+ AgentsRail when a run is live) │ Editor │ RtlVis +  ─┤
 *   │                                              │        │ file chat │
 *   ├─ Bottom: Output · Telemetry · Diff · Tests ──────────────────────┤
 *   └─ Status bar ──────────────────────────────────────────────────────┘
 *
 * Deliberate omissions: the IDE does NOT show last-run KPIs, mental-model
 * previews, or "Start verification" CTAs. Those questions belong in the
 * conversation — the IDE answers "what is in this file" and "what is the
 * machine doing right now", nothing else.
 */
export default function IdeWorkspace({
  open,
  onClose,
  projectId,
  projectName,
  authToken,
  files = [],
  activeFileId,
  onSelectFile,
  onChangeFile,
  onSaveFile,
  output = [],
  events = [],
  runStatus,
  runs = [],
  recentDiff = null,
  onAskAssistant,
  assistantMessages = [],
  threadTitle = "Project Chat",
  linkedMainThreadTitle = "",
  assistantLoading = false,
  pendingAttachments = [],
  onRemovePendingAttachment,
  onAttachFromExplorer,
  onOpenWorkspaceAttach,
  onAttachUpload,
  onRenameFile,
  onDeleteFile,
  onUploadFiles,
  explorerBusy = false,
  explorerError = "",
  busy = false,
  onCancelStream,
  onOpenPalette,
  onOpenRun,
  onOpenDiffInThread,
  isDarkTheme = true,
  tokenContext = null,
  inlineCompletion = null,
  lintOptions = null,
  completionDebug = null,
}) {
  const [bottomTab, setBottomTab] = useState("output");
  const [bottomOpen, setBottomOpen] = useState(false);
  const [ask, setAsk] = useState("");
  const [savedFlash, setSavedFlash] = useState(false);
  const [dropActive, setDropActive] = useState(false);
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const [rightRail, setRightRail] = useState("signals");
  const [ctxMenu, setCtxMenu] = useState(null);
  const [renamingId, setRenamingId] = useState(null);
  const [renameDraft, setRenameDraft] = useState("");
  const editorRef = useRef(null);
  const msgsRef = useRef(null);
  const attachWrapRef = useRef(null);
  const uploadInputRef = useRef(null);
  const renameInputRef = useRef(null);
  const [inlineCompletionOn, setInlineCompletionOn] = useState(() => isInlineCompletionEnabled());
  const [fileSearch, setFileSearch] = useState("");
  const fileSearchRef = useRef(null);

  const completionOptions = useMemo(() => {
    if (!inlineCompletion || !inlineCompletionOn) return null;
    return {
      ...inlineCompletion,
      enabled: inlineCompletionOn,
    };
  }, [inlineCompletion, inlineCompletionOn]);

  const editorLintOptions = useMemo(() => {
    if (!lintOptions) return null;
    return {
      ...lintOptions,
      enabled: lintOptions.enabled !== false,
    };
  }, [lintOptions]);

  useEffect(() => {
    if (!open) setFileSearch("");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        const file = files.find((f) => f.id === activeFileId);
        if (file) {
          Promise.resolve(onSaveFile?.(file))
            .then(() => {
              setSavedFlash(true);
              setTimeout(() => setSavedFlash(false), 1400);
            })
            .catch(() => {});
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, files, activeFileId, onSaveFile]);

  useEffect(() => {
    if (!attachMenuOpen) return;
    const fn = (e) => {
      if (!attachWrapRef.current?.contains(e.target)) setAttachMenuOpen(false);
    };
    document.addEventListener("mousedown", fn);
    return () => document.removeEventListener("mousedown", fn);
  }, [attachMenuOpen]);

  useEffect(() => {
    const el = msgsRef.current;
    if (!el) return;
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
  }, [assistantMessages.length, busy]);

  const activeFile = useMemo(
    () => files.find((f) => f.id === activeFileId) || null,
    [files, activeFileId],
  );

  const grouped = useMemo(() => {
    const generated = files.filter((f) => String(f.artifactType || "").toLowerCase() === "generated");
    const spec = files.filter((f) => (
      String(f.artifactType || "").toLowerCase() !== "generated"
      && /\.(md|txt|pdf|docx)$/i.test(f.name || "")
    ));
    const rtl = files.filter((f) => (
      String(f.artifactType || "").toLowerCase() !== "generated"
      && /\.(sv|svh|v|vh)$/i.test(f.name || "")
    ));
    const known = new Set([...generated, ...spec, ...rtl]);
    const other = files.filter((f) => !known.has(f));
    return { Specifications: spec, "RTL Sources": rtl, "Generated Outputs": generated, Other: other };
  }, [files]);

  const filteredGrouped = useMemo(() => {
    const q = fileSearch.trim().toLowerCase();
    if (!q) return grouped;
    const filterList = (list) => list.filter((f) => {
      const name = String(f.name || "").toLowerCase();
      const path = String(f.path || "").toLowerCase();
      const member = String(f.memberPath || "").toLowerCase();
      return name.includes(q) || path.includes(q) || member.includes(q);
    });
    return {
      Specifications: filterList(grouped.Specifications),
      "RTL Sources": filterList(grouped["RTL Sources"]),
      "Generated Outputs": filterList(grouped["Generated Outputs"]),
      Other: filterList(grouped.Other),
    };
  }, [grouped, fileSearch]);

  const fileSearchCount = useMemo(
    () => Object.values(filteredGrouped).reduce((sum, list) => sum + list.length, 0),
    [filteredGrouped],
  );

  const rightShowsRtl = Boolean(activeFile && isRtlFile(activeFile.name));

  useEffect(() => {
    if (!open) return;
    setRightRail(rightShowsRtl ? "signals" : "assistant");
  }, [open, rightShowsRtl]);

  const pickRightRail = useCallback((rail) => {
    if (rail !== "signals" && rail !== "assistant") return;
    setRightRail((prev) => {
      if (prev === rail) return prev;
      playTick({ pitch: rail === "assistant" ? "high" : "low" });
      pulseHaptic(6);
      return rail;
    });
  }, []);

  const openExplorerContextMenu = useCallback((e, file = null) => {
    e.preventDefault();
    e.stopPropagation();
    const menuW = 220;
    const menuH = file ? 120 : 48;
    const x = Math.min(e.clientX, window.innerWidth - menuW - 8);
    const y = Math.min(e.clientY, window.innerHeight - menuH - 8);
    setCtxMenu({ x, y, file });
  }, []);

  const beginRename = useCallback((file) => {
    if (!file?.id) return;
    setRenamingId(file.id);
    setRenameDraft(file.name || "");
    requestAnimationFrame(() => renameInputRef.current?.select());
  }, []);

  const commitRename = useCallback(async (file) => {
    if (!file?.id) return;
    const trimmed = renameDraft.trim();
    setRenamingId(null);
    if (!trimmed || trimmed === file.name) return;
    await onRenameFile?.(file, trimmed);
  }, [renameDraft, onRenameFile]);

  const handleExplorerDelete = useCallback((file) => {
    if (!file?.name) return;
    const ok = window.confirm(
      `Delete "${file.name}" from this project? This removes it from the server.`,
    );
    if (ok) void onDeleteFile?.(file);
  }, [onDeleteFile]);

  const cancelRename = useCallback(() => {
    setRenamingId(null);
    setRenameDraft("");
  }, []);

  const triggerExplorerUpload = useCallback(() => {
    uploadInputRef.current?.click();
  }, []);

  const handleExplorerUploadPicked = useCallback((e) => {
    const list = e.target.files;
    e.target.value = "";
    if (list?.length) onUploadFiles?.(list);
  }, [onUploadFiles]);

  const handleExplorerDragStart = useCallback((e, file) => {
    if (!file?.artifactId) return;
    e.dataTransfer.setData(ARTIFACT_DRAG_MIME, JSON.stringify({
      artifactId: file.artifactId,
      label: file.name,
      memberPath: file.memberPath || (file.path && file.path !== file.name ? file.path : undefined),
    }));
    e.dataTransfer.effectAllowed = "copy";
  }, []);

  const handleDropAttach = useCallback((e) => {
    e.preventDefault();
    setDropActive(false);
    const parsed = parseArtifactDrag(e.dataTransfer);
    if (parsed) {
      onAttachFromExplorer?.(parsed);
      return;
    }
    if (e.dataTransfer?.files?.length && onAttachUpload) {
      onAttachUpload();
    }
  }, [onAttachFromExplorer, onAttachUpload]);

  const submitAsk = useCallback(() => {
    if (busy) return;
    const trimmed = ask.trim();
    if (!trimmed && !pendingAttachments.length) return;
    setRightRail("assistant");
    void onAskAssistant?.(trimmed, activeFile);
    setAsk("");
  }, [ask, pendingAttachments.length, busy, onAskAssistant, activeFile]);

  const canSend = Boolean(ask.trim() || pendingAttachments.length);
  const liveTokenContext = useMemo(() => {
    if (!tokenContext) return null;
    const askTokens = estimateTokensFromText(ask);
    const convRow = (tokenContext.breakdown || []).find((r) => r.id === "conversation");
    const attachRow = (tokenContext.breakdown || []).find((r) => r.id === "attachments");
    const conversationTokens = Number(convRow?.tokens || 0);
    const attachmentTokens = Number(attachRow?.tokens || 0);
    const breakdown = [
      { id: "conversation", label: "Conversation", tokens: conversationTokens, color: "#6b8fd4" },
      { id: "composer", label: "This message", tokens: askTokens, color: "#5a9e6f" },
      { id: "attachments", label: "Attachments", tokens: attachmentTokens, color: "#c9a04a" },
    ].filter((row) => row.tokens > 0);
    const usedTokens = breakdown.reduce((sum, row) => sum + row.tokens, 0);
    const budget = Number(tokenContext.budgetTokens || 0);
    return {
      ...tokenContext,
      usedTokens,
      currentInputTokens: askTokens + attachmentTokens,
      breakdown,
      percent: budget > 0 ? (usedTokens / budget) * 100 : 0,
    };
  }, [ask, tokenContext]);

  const { closeButtonProps } = useOverlayClose({ open, onClose, closeOnEsc: false, label: "Close editor" });

  if (!open) return null;

  return (
    <div className="tf-ide open" role="dialog" aria-label="IDE workspace">
      <header className="tf-ide-h">
        <span className="badge">IDE</span>
        <span className="title">{activeFile?.name || projectName || "Workspace"}</span>
        {activeFile?.path && activeFile.path !== activeFile.name
          ? <span className="sub">{activeFile.path}</span> : null}
        <span className="spacer" />
        {savedFlash ? <span className="tf-tool-badge ok">Saved</span> : null}
        <button className="tf-btn sm" onClick={() => onOpenPalette?.()} title="Command palette (⌘K)">
          <Icon.Search width="12" height="12" /> Palette <span className="tf-kbd">⌘K</span>
        </button>
        <button className="tf-btn sm" onClick={() => { setBottomTab("output"); setBottomOpen((v) => !v); }}>
          <Icon.Terminal width="12" height="12" /> {bottomOpen ? "Hide" : "Show"} output
        </button>
        <button {...closeButtonProps}>
          <Icon.Close width="14" height="14" />
        </button>
      </header>

      <div className="tf-ide-body">
        {/* ── Left: explorer ─────────────────────────────────────── */}
        <aside
          className="tf-ide-explorer"
          onContextMenu={(e) => openExplorerContextMenu(e, null)}
        >
          <div className="tf-ide-explorer-head">
            <div className="tf-ide-explorer-toolbar">
              <span className="tf-ide-explorer-title">Files</span>
              <span className="spacer" />
              <button
                type="button"
                className="tf-btn sm"
                disabled={explorerBusy || !onUploadFiles}
                title="Upload files to this project"
                onClick={triggerExplorerUpload}
              >
                <Icon.Upload width="12" height="12" /> Upload
              </button>
              <input
                ref={uploadInputRef}
                type="file"
                multiple
                className="tf-sr-only"
                tabIndex={-1}
                aria-hidden
                onChange={handleExplorerUploadPicked}
              />
            </div>
            {files.length > 0 ? (
              <div className="tf-ide-explorer-search">
                <Icon.Search width="13" height="13" />
                <input
                  ref={fileSearchRef}
                  type="search"
                  placeholder="Filter files…"
                  value={fileSearch}
                  onChange={(e) => setFileSearch(e.target.value)}
                  aria-label="Search project files"
                />
                {fileSearch ? (
                  <>
                    <span className="tf-ide-explorer-search-count" aria-live="polite">
                      {fileSearchCount}
                    </span>
                    <button
                      type="button"
                      className="tf-ide-explorer-search-clear"
                      aria-label="Clear search"
                      onClick={() => {
                        setFileSearch("");
                        fileSearchRef.current?.focus();
                      }}
                    >
                      <Icon.X width="12" height="12" />
                    </button>
                  </>
                ) : null}
              </div>
            ) : null}
          </div>
          {explorerError ? (
            <div className="tf-ide-explorer-error" role="alert">{explorerError}</div>
          ) : null}
          {files.length > 0 && fileSearch.trim() && fileSearchCount === 0 ? (
            <div className="tf-ide-explorer-empty">
              No files match &ldquo;{fileSearch.trim()}&rdquo;.
            </div>
          ) : null}
          {Object.entries(filteredGrouped).map(([label, list]) => (
            list.length === 0 ? null : (
              <div key={label}>
                <div className="grp">{label}</div>
                {list.map((f) => (
                  <div
                    key={f.id}
                    className={`erow-wrap ${f.id === activeFile?.id ? "active" : ""}`}
                  >
                    {renamingId === f.id ? (
                      <form
                        className="erow erow-rename"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void commitRename(f);
                        }}
                      >
                        <input
                          ref={renameInputRef}
                          className="tf-ide-rename-input"
                          value={renameDraft}
                          onChange={(e) => setRenameDraft(e.target.value)}
                          onBlur={() => void commitRename(f)}
                          onKeyDown={(e) => {
                            if (e.key === "Escape") {
                              e.preventDefault();
                              cancelRename();
                            }
                          }}
                          aria-label="New file name"
                        />
                      </form>
                    ) : (
                      <button
                        type="button"
                        className={`erow ${f.id === activeFile?.id ? "active" : ""}`}
                        draggable
                        title="Right-click for rename/delete · drag to attach"
                        onDragStart={(e) => handleExplorerDragStart(e, f)}
                        onClick={() => onSelectFile?.(f.id)}
                        onContextMenu={(e) => openExplorerContextMenu(e, f)}
                      >
                        <span className={`gly${getEditorFileKind(f.name) === "pdf" ? " pdf" : ""}`} aria-hidden>
                          {getEditorFileKind(f.name) === "pdf" ? (
                            <Icon.Book width="12" height="12" />
                          ) : (
                            <Icon.File width="12" height="12" />
                          )}
                        </span>
                        <span className="ename">{f.name}</span>
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )
          ))}
          {files.length === 0 ? (
            <div className="tf-ide-explorer-empty">
              No project files yet. Upload spec or RTL here, or attach files from the conversation.
              <button
                type="button"
                className="tf-btn sm tf-ide-explorer-upload-cta"
                disabled={explorerBusy || !onUploadFiles}
                onClick={triggerExplorerUpload}
              >
                <Icon.Upload width="12" height="12" /> Upload files
              </button>
            </div>
          ) : null}
        </aside>

        {/* ── Center: editor / overview + bottom tabs ─────────────── */}
        <section className="tf-ide-center">
          <div className="tf-ide-tabs">
            {activeFile ? (
              <span className="tf-ide-tab active">
                <Icon.File width="12" height="12" />
                {activeFile.name}
              </span>
            ) : null}
          </div>

          <div className="tf-ide-editor-wrap">
            {activeFile ? (
              activeFile.loaded ? (
                <ArtifactDocumentPane
                  file={activeFile}
                  isDarkTheme={isDarkTheme}
                  onChange={(value) => onChangeFile?.(activeFile.id, value)}
                  onMount={(editor) => { editorRef.current = editor; }}
                  completionOptions={completionOptions}
                  lintOptions={editorLintOptions}
                />
              ) : (
                <div className="tf-ide-editor-loading">Loading file…</div>
              )
            ) : (
              <div className="tf-ide-empty">
                <div className="tf-ide-empty-inner">
                  <Icon.File width="16" height="16" />
                  <p>
                    Pick a file on the left to start editing.
                    {files.length === 0 ? " Attach RTL or spec files from the conversation and they'll show up here." : ""}
                  </p>
                  <p className="tf-ide-empty-hint">Esc to return to the conversation.</p>
                </div>
              </div>
            )}
          </div>

          {/* Bottom tab strip — Output · Telemetry · Diff · Tests */}
          <div className={`tf-ide-bottom ${bottomOpen ? "open" : ""}`}>
            <div className="tabs">
              {BOTTOM_TABS.map((t) => {
                const IconCmp = Icon[t.icon] || Icon.File;
                const active = bottomTab === t.id && bottomOpen;
                return (
                  <button
                    key={t.id}
                    className={`tab ${active ? "active" : ""}`}
                    onClick={() => {
                      if (bottomTab === t.id && bottomOpen) {
                        setBottomOpen(false);
                      } else {
                        setBottomTab(t.id);
                        setBottomOpen(true);
                      }
                    }}
                  >
                    <IconCmp width="11" height="11" />
                    {t.label}
                    {t.id === "telemetry" && events.length ? <span className="dot-count">{Math.min(events.length, 99)}</span> : null}
                    {t.id === "diff" && recentDiff ? <span className="dot live" /> : null}
                  </button>
                );
              })}
              <span className="spacer" />
              {runStatus ? <span className="run-meta">run · {runStatus}</span> : null}
            </div>
            {bottomOpen ? (
              <div className="body">
                {bottomTab === "output" ? (
                  output.length === 0 ? (
                    <div className="tf-ide-pane-empty">No output yet. The active run streams logs here.</div>
                  ) : (
                    <div className="tf-ide-output-body">
                      {output.map((line, i) => (
                        <div key={i} className={`ln ${line.level || ""}`}>
                          <span className="ts">{fmtTs(line.ts)}</span>
                          {line.message}
                        </div>
                      ))}
                    </div>
                  )
                ) : null}
                {bottomTab === "telemetry" ? <TelemetryPane events={events} /> : null}
                {bottomTab === "diff" ? <DiffPane recentDiff={recentDiff} onOpenInThread={onOpenDiffInThread} /> : null}
                {bottomTab === "tests" ? <TestsPane runs={runs} onOpenRun={onOpenRun} /> : null}
              </div>
            ) : null}
          </div>
        </section>

        {/* ── Right: Signals (RTL viz) ↔ Assistant ── */}
        <aside className="tf-ide-right">
          <div className="tf-ide-right-tabs">
            {rightShowsRtl ? (
              <div
                className="tf-mode-toggle tf-ide-rail-toggle"
                role="tablist"
                aria-label="IDE right panel"
                data-rail={rightRail}
              >
                <span className="tf-mode-thumb" aria-hidden />
                <button
                  type="button"
                  role="tab"
                  aria-selected={rightRail === "signals"}
                  className={`tf-mode-opt${rightRail === "signals" ? " active" : ""}`}
                  onClick={() => pickRightRail("signals")}
                >
                  <span className="tf-mode-dot tf-mode-dot--design" aria-hidden />
                  <Icon.Wave width="12" height="12" />
                  <span className="tf-mode-label">Signals</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={rightRail === "assistant"}
                  className={`tf-mode-opt${rightRail === "assistant" ? " active" : ""}`}
                  onClick={() => pickRightRail("assistant")}
                >
                  <span className="tf-mode-dot tf-mode-dot--verify" aria-hidden />
                  <Icon.Sparkles width="12" height="12" />
                  <span className="tf-mode-label">Assistant</span>
                </button>
              </div>
            ) : (
              <span className="tf-ide-rail-solo-label">Assistant</span>
            )}
            {rightRail === "assistant" ? (
              <span
                className="tf-ide-thread-chip"
                title="Separate IDE thread in the database; main conversation is read-only context"
              >
                ↗ {linkedMainThreadTitle || threadTitle}
              </span>
            ) : null}
          </div>

          {rightShowsRtl && rightRail === "signals" ? (
            <div className="tf-ide-right-panel tf-ide-right-rtl">
              <RtlVisualizer
                rtlCode={activeFile.content || ""}
                artifactLabel={activeFile.name}
                projectId={projectId}
                authToken={authToken}
              />
            </div>
          ) : null}

          {rightRail === "assistant" ? (
            <div className="tf-ide-right-panel tf-ide-right-assistant">
              <div className="tf-ide-assistant-head">
                <span className="grp">IDE assistant</span>
              </div>
              <div className="tf-ide-right-msgs" ref={msgsRef}>
                {assistantLoading ? (
                  <div className="ai-msg tf-ide-assistant-hint">Opening IDE thread…</div>
                ) : null}
                {!assistantLoading && assistantMessages.length === 0 ? (
                  <div className="ai-msg tf-ide-assistant-hint">
                    {activeFile
                      ? "Ask or edit this file. Messages stay in a separate IDE thread; the main chat is context only."
                      : "Pick a file or attach context. This panel does not post to your main conversation."}
                  </div>
                ) : null}
                {assistantMessages.map((m, i) => {
                  const tokens = totalTokensFromUsage(m.tokenUsage);
                  const isUser = m.role === "user";
                  return (
                    <div key={`ide-msg-${i}`} className={`ai-msg${isUser ? " you" : " assistant"}`}>
                      {!isUser && tokens > 0 ? (
                        <div className="tf-ide-msg-meta">
                          <span className="tf-ide-token-chip">
                            {formatTokenCount(tokens)} tokens
                          </span>
                        </div>
                      ) : null}
                      {isUser ? (
                        <span className="tf-ide-msg-plain">{m.text}</span>
                      ) : (
                        <TfMarkdown className="tf-ide-md">{m.text}</TfMarkdown>
                      )}
                    </div>
                  );
                })}
                {busy ? (
                  <div className="ai-msg tf-ide-assistant-busy">
                    <span className="dots"><span /><span /><span /></span>
                    Working…
                  </div>
                ) : null}
              </div>
              <div
                className={`tf-ide-right-input${dropActive ? " drop-active" : ""}`}
                onDragOver={(e) => { e.preventDefault(); setDropActive(true); e.dataTransfer.dropEffect = "copy"; }}
                onDragLeave={() => setDropActive(false)}
                onDrop={handleDropAttach}
              >
                {pendingAttachments.length > 0 ? (
                  <div className="tf-pending-attach tf-ide-pending-attach">
                    {pendingAttachments.map((a) => (
                      <div key={a.key} className="tf-pending-chip" title={a.label}>
                        <span>{a.label}</span>
                        <button type="button" aria-label={`Remove ${a.label}`} onClick={() => onRemovePendingAttachment?.(a.key)}>×</button>
                      </div>
                    ))}
                  </div>
                ) : null}
                <p className="tf-ide-drop-hint">Drag explorer files here · up to 4 attachments per message</p>
                <div className={`tf-composer tf-ide-composer${busy ? " busy" : ""}`}>
                  <div className="tf-attach-wrap" ref={attachWrapRef}>
                    <button
                      type="button"
                      className="tool"
                      title="Attach context"
                      disabled={busy}
                      aria-expanded={attachMenuOpen}
                      onClick={() => setAttachMenuOpen((v) => !v)}
                    >
                      <Icon.Paperclip width="16" height="16" />
                    </button>
                    {attachMenuOpen && !busy ? (
                      <div className="tf-attach-menu" role="menu">
                        <button type="button" role="menuitem" onClick={() => { setAttachMenuOpen(false); onOpenWorkspaceAttach?.(); }}>
                          Pick from project…
                        </button>
                        <button type="button" role="menuitem" onClick={() => { setAttachMenuOpen(false); onAttachUpload?.(); }}>
                          Upload from computer…
                        </button>
                      </div>
                    ) : null}
                  </div>
                  <textarea
                    rows={1}
                    placeholder={activeFile ? "Ask or request an edit to this file…" : "Ask about the project…"}
                    value={ask}
                    disabled={busy}
                    onChange={(e) => setAsk(e.target.value)}
                    onKeyDown={(e) => {
                      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                        e.preventDefault();
                        submitAsk();
                        return;
                      }
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        submitAsk();
                      }
                    }}
                  />
                  <div className="tf-composer-trailing">
                    <ContextUsageMeter tokenContext={liveTokenContext} fixedPanel />
                    {busy ? (
                      <button type="button" className="send cancel" onClick={() => onCancelStream?.()} title="Cancel">
                        <Icon.X width="14" height="14" />
                      </button>
                    ) : (
                      <button type="button" className="send" disabled={!canSend} onClick={submitAsk}>
                        <Icon.Send width="12" height="12" />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>
          ) : null}
        </aside>

      </div>

      <div className="tf-ide-status">
        <span className="dot" />
        <span>Connected</span>
        {inlineCompletion ? (
          <span
            className={`tf-ide-completion-debug tf-ide-completion-debug--${completionDebug?.phase || "idle"}`}
            title={
              completionDebug?.lastError
              || completionDebug?.detail
              || (completionDebug?.log?.[0] ? JSON.stringify(completionDebug.log[0]) : "Tab completion debug")
            }
          >
            {formatCompletionDebugLine(completionDebug)}
          </span>
        ) : null}
        {inlineCompletion ? (
          <button
            type="button"
            className={`tf-ide-completion-toggle${inlineCompletionOn ? " on" : ""}`}
            title="Toggle inline tab completion"
            onClick={() => {
              setInlineCompletionOn((prev) => {
                const next = !prev;
                setInlineCompletionEnabled(next);
                return next;
              });
            }}
          >
            Tab complete {inlineCompletionOn ? "on" : "off"}
          </button>
        ) : null}
        <span style={{ marginLeft: "auto" }}>{files.length} file{files.length === 1 ? "" : "s"} · ⌘S save · Esc back</span>
      </div>

      <IdeExplorerContextMenu
        open={Boolean(ctxMenu)}
        x={ctxMenu?.x ?? 0}
        y={ctxMenu?.y ?? 0}
        file={ctxMenu?.file ?? null}
        busy={explorerBusy}
        onClose={() => setCtxMenu(null)}
        onRename={beginRename}
        onDelete={handleExplorerDelete}
        onUpload={triggerExplorerUpload}
      />
    </div>
  );
}
