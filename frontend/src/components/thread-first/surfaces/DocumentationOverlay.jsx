import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Icon from "../icons";
import useOverlayClose from "../useOverlayClose";
import API_BASE_URL from "../../../config";
import {
  defaultDocName,
  enrichDocEntry,
  groupDocsForSidebar,
  isCatalogDocName,
  isUserFacingDoc,
} from "../documentationCatalog";

function slugifyHeading(text) {
  const raw = Array.isArray(text)
    ? text.map((c) => (typeof c === "string" ? c : "")).join("")
    : text;
  return String(raw || "")
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .trim();
}

function extractToc(markdown) {
  const lines = String(markdown || "").split("\n");
  const items = [];
  for (const line of lines) {
    const m = /^(#{2,3})\s+(.+)$/.exec(line.trim());
    if (!m) continue;
    const level = m[1].length;
    const text = m[2].replace(/\s+#+\s*$/, "").trim();
    const id = slugifyHeading(text);
    if (id) items.push({ id, text, level });
  }
  return items;
}

function stripLeadingH1(markdown, title) {
  const lines = String(markdown || "").split("\n");
  if (!lines.length) return markdown;
  const first = lines[0].trim();
  if (first.startsWith("# ")) {
    const h1 = first.slice(2).trim();
    if (!title || h1.toLowerCase() === String(title).toLowerCase()) {
      return lines.slice(1).join("\n").replace(/^\s+/, "");
    }
  }
  return markdown;
}

/** Plain-text tooltip from API blurbs that may contain **markdown** emphasis. */
function plainDocTooltip(description, displayTitle) {
  const raw = String(description || displayTitle || "").trim();
  if (!raw) return displayTitle || "";
  return raw.replace(/\*\*([^*]+)\*\*/g, "$1").replace(/\*([^*]+)\*/g, "$1");
}

/** Poll while open so guide updates appear without restarting the app. */
const DOC_POLL_MS = 15000;

function indexRevisionMap(docs) {
  const map = new Map();
  for (const d of docs || []) {
    if (d?.name) map.set(d.name, d.updated_at ?? 0);
  }
  return map;
}

function indexRevisionsDiffer(prev, next) {
  if (prev.size !== next.size) return true;
  for (const [name, ts] of next) {
    if (prev.get(name) !== ts) return true;
  }
  return false;
}

/**
 * DocumentationOverlay — in-app Chipix Studio user guides (strict catalog allowlist).
 */
export default function DocumentationOverlay({ open, onClose, authToken }) {
  const [index, setIndex] = useState([]);
  const [selectedName, setSelectedName] = useState("");
  const [selectedTitle, setSelectedTitle] = useState("");
  const [selectedContent, setSelectedContent] = useState("");
  const [loadingIndex, setLoadingIndex] = useState(false);
  const [loadingDoc, setLoadingDoc] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [updateHint, setUpdateHint] = useState("");
  const indexRevisionRef = useRef(new Map());
  const selectedNameRef = useRef("");

  const authHeaders = useMemo(
    () => (authToken ? { Authorization: `Bearer ${authToken}` } : {}),
    [authToken],
  );

  const userDocs = useMemo(() => index.filter(isUserFacingDoc), [index]);

  const sections = useMemo(() => {
    const q = query.trim().toLowerCase();
    const grouped = groupDocsForSidebar(userDocs);
    if (!q) return grouped;
    return grouped
      .map((section) => ({
        ...section,
        items: section.items.filter((d) => {
          const hay = `${d.displayTitle} ${d.title} ${d.description || ""} ${d.name}`.toLowerCase();
          return hay.includes(q);
        }),
      }))
      .filter((s) => s.items.length > 0);
  }, [userDocs, query]);

  const flatFiltered = useMemo(
    () => sections.flatMap((s) => s.items),
    [sections],
  );

  const toc = useMemo(
    () => extractToc(selectedContent),
    [selectedContent],
  );

  const bodyMarkdown = useMemo(
    () => stripLeadingH1(selectedContent, selectedTitle),
    [selectedContent, selectedTitle],
  );

  const activeEntry = useMemo(
    () => (selectedName ? enrichDocEntry(userDocs.find((d) => d.name === selectedName) || { name: selectedName }) : null),
    [userDocs, selectedName],
  );

  const displayTitle = activeEntry?.displayTitle || selectedTitle;

  useEffect(() => {
    selectedNameRef.current = selectedName;
  }, [selectedName]);

  const applyIndex = useCallback((docs) => {
    setIndex(docs);
    indexRevisionRef.current = indexRevisionMap(docs);
    setSelectedName((prev) => {
      if (prev && docs.some((d) => d.name === prev)) return prev;
      return defaultDocName(docs);
    });
  }, []);

  const loadIndex = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoadingIndex(true);
    if (!silent) setError("");
    try {
      const r = await fetch(`${API_BASE_URL}/api/v1/docs/index`, { headers: authHeaders });
      if (!r.ok) throw new Error(`Index fetch failed (${r.status})`);
      const data = await r.json();
      const docs = (Array.isArray(data?.docs) ? data.docs : []).filter(isUserFacingDoc);
      applyIndex(docs);
    } catch (e) {
      if (!silent) setError(e?.message || "Couldn't load the documentation index.");
    } finally {
      if (!silent) setLoadingIndex(false);
    }
  }, [authHeaders, applyIndex]);

  const loadDoc = useCallback(async (name, { silent = false } = {}) => {
    if (!name || !isCatalogDocName(name)) {
      setSelectedTitle("");
      setSelectedContent("");
      if (!silent) setError("That guide is not available.");
      return;
    }
    if (!silent) setLoadingDoc(true);
    if (!silent) setError("");
    try {
      const r = await fetch(`${API_BASE_URL}/api/v1/docs/${encodeURIComponent(name)}`, {
        headers: authHeaders,
      });
      if (!r.ok) throw new Error(`Doc fetch failed (${r.status})`);
      const data = await r.json();
      setSelectedTitle(data?.title || name);
      setSelectedContent(data?.content || "");
      if (data?.updated_at != null) {
        const next = new Map(indexRevisionRef.current);
        next.set(name, data.updated_at);
        indexRevisionRef.current = next;
      }
    } catch (e) {
      if (!silent) {
        setError(e?.message || "Couldn't load this document.");
        setSelectedContent("");
      }
    } finally {
      if (!silent) setLoadingDoc(false);
    }
  }, [authHeaders]);

  const pollDocs = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE_URL}/api/v1/docs/index`, { headers: authHeaders });
      if (!r.ok) return;
      const data = await r.json();
      const docs = (Array.isArray(data?.docs) ? data.docs : []).filter(isUserFacingDoc);
      const nextRevision = indexRevisionMap(docs);
      const prevRevision = indexRevisionRef.current;
      if (!indexRevisionsDiffer(prevRevision, nextRevision)) return;

      const activeName = selectedNameRef.current;
      const activeChanged = activeName
        && prevRevision.has(activeName)
        && nextRevision.get(activeName) !== prevRevision.get(activeName);

      applyIndex(docs);
      if (activeChanged) {
        await loadDoc(activeName, { silent: true });
        setUpdateHint("This guide was updated.");
      } else if (prevRevision.size !== nextRevision.size) {
        setUpdateHint("The guide list was updated.");
      }
    } catch {
      /* polling is best-effort */
    }
  }, [authHeaders, applyIndex, loadDoc]);

  const handleRefresh = useCallback(() => {
    setUpdateHint("");
    void loadIndex();
    if (selectedNameRef.current) void loadDoc(selectedNameRef.current);
  }, [loadIndex, loadDoc]);

  const selectDoc = useCallback((name) => {
    if (!isCatalogDocName(name)) return;
    setSelectedName(name);
    if (typeof window !== "undefined" && window.matchMedia("(max-width: 900px)").matches) {
      setSidebarOpen(false);
    }
  }, []);

  const scrollToHeading = useCallback((id) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setSidebarOpen(true);
    setUpdateHint("");
    indexRevisionRef.current = new Map();
    void loadIndex();
  }, [open, loadIndex]);

  useEffect(() => {
    if (!open || !selectedName) return;
    void loadDoc(selectedName);
  }, [open, selectedName, loadDoc]);

  useEffect(() => {
    if (!open) return undefined;
    const id = window.setInterval(() => { void pollDocs(); }, DOC_POLL_MS);
    return () => window.clearInterval(id);
  }, [open, pollDocs]);

  const { closeButtonProps } = useOverlayClose({ open, onClose, closeOnEsc: false, label: "Close documentation" });

  const markdownComponents = useMemo(
    () => ({
      h2: ({ children, ...props }) => {
        const id = slugifyHeading(children);
        return <h2 id={id} {...props}>{children}</h2>;
      },
      h3: ({ children, ...props }) => {
        const id = slugifyHeading(children);
        return <h3 id={id} {...props}>{children}</h3>;
      },
      a: ({ href, children, ...props }) => {
        const raw = String(href || "");
        if (raw.endsWith(".md")) {
          const name = raw.split("/").pop();
          if (!isCatalogDocName(name)) return <span>{children}</span>;
          return (
            <button
              type="button"
              className="tf-docs-inline-link"
              onClick={() => selectDoc(name)}
            >
              {children}
            </button>
          );
        }
        return (
          <a href={href} target="_blank" rel="noreferrer noopener" {...props}>
            {children}
          </a>
        );
      },
    }),
    [selectDoc],
  );

  if (!open) return null;

  const resultCount = flatFiltered.length;

  return (
    <div
      className="tf-overlay tf-docs-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Documentation"
    >
      <header className="tf-overlay-head tf-docs-head">
        <div className="tf-overlay-title">
          <Icon.Book width="18" height="18" />
          <span>Documentation</span>
          <span className="tf-docs-head-sub">Chipix Studio guides</span>
        </div>
        <div className="tf-overlay-actions">
          <button
            type="button"
            className="tf-btn sm ghost tf-docs-refresh"
            onClick={handleRefresh}
            disabled={loadingIndex || loadingDoc}
            aria-label="Refresh guides"
            title="Reload guides"
          >
            Refresh
          </button>
          <button
            type="button"
            className="tf-btn sm ghost tf-docs-toggle-nav"
            onClick={() => setSidebarOpen((v) => !v)}
            aria-expanded={sidebarOpen}
          >
            {sidebarOpen ? "Hide list" : "Show list"}
          </button>
          <button type="button" {...closeButtonProps}>
            <Icon.Close width="14" height="14" />
          </button>
        </div>
      </header>

      <div className={`tf-docs-body${sidebarOpen ? "" : " nav-collapsed"}`}>
        <aside className={`tf-docs-sidebar${sidebarOpen ? "" : " collapsed"}`} aria-label="Guide list">
          <div className="tf-docs-search">
            <Icon.Search width="13" height="13" />
            <input
              type="search"
              placeholder="Search guides…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search documentation"
            />
            {query ? (
              <span className="tf-docs-search-count" aria-live="polite">
                {resultCount}
              </span>
            ) : null}
          </div>

          <div className="tf-docs-sidebar-scroll">
            {loadingIndex ? (
              <div className="tf-docs-empty">Loading guides…</div>
            ) : sections.length === 0 ? (
              <div className="tf-docs-empty">
                {query ? (
                  <>No guides match &ldquo;{query}&rdquo;.</>
                ) : (
                  <>
                    <p>No guides are available right now.</p>
                    <p className="tf-docs-empty-hint">
                      Try <strong>Refresh</strong>, or ask your administrator if documentation should be enabled for your account.
                    </p>
                  </>
                )}
              </div>
            ) : (
              sections.map((section) => (
                <div key={section.id} className="tf-docs-section">
                  <div className="tf-docs-section-label">{section.label}</div>
                  <nav className="tf-docs-list">
                    {section.items.map((d) => (
                      <button
                        key={d.name}
                        type="button"
                        className={`tf-docs-item${selectedName === d.name ? " active" : ""}`}
                        onClick={() => selectDoc(d.name)}
                        title={plainDocTooltip(d.description, d.displayTitle)}
                      >
                        <span className="tf-docs-glyph" aria-hidden>
                          {d.glyph}
                        </span>
                        <span className="tf-docs-item-text">
                          <span className="t">{d.displayTitle}</span>
                        </span>
                      </button>
                    ))}
                  </nav>
                </div>
              ))
            )}
          </div>
        </aside>

        <section className="tf-docs-main" aria-busy={loadingDoc ? "true" : "false"}>
          {updateHint ? (
            <div className="tf-docs-update-hint" role="status">
              <span>{updateHint}</span>
              <button
                type="button"
                className="tf-btn sm ghost"
                onClick={() => setUpdateHint("")}
              >
                Dismiss
              </button>
            </div>
          ) : null}
          {error ? (
            <div className="tf-docs-error" role="alert">
              <Icon.Warning width="14" height="14" />
              <span>{error}</span>
              <button
                type="button"
                className="tf-btn sm"
                onClick={() => (selectedName ? loadDoc(selectedName) : loadIndex())}
              >
                Try again
              </button>
            </div>
          ) : null}

          <div className="tf-docs-reader-wrap">
            <article className="tf-docs-reader">
              {loadingDoc ? (
                <div className="tf-docs-loading">Loading {displayTitle || selectedName}…</div>
              ) : selectedContent ? (
                <>
                  <header className="tf-docs-article-head">
                    <h1>{displayTitle || selectedTitle}</h1>
                    {activeEntry?.description ? (
                      <p className="tf-docs-lead">{activeEntry.description}</p>
                    ) : null}
                  </header>
                  <div className="tf-docs-prose">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                      {bodyMarkdown}
                    </ReactMarkdown>
                  </div>
                </>
              ) : !error ? (
                <div className="tf-docs-empty pad">Choose a guide from the list.</div>
              ) : null}
            </article>

            {toc.length > 0 && selectedContent && !loadingDoc ? (
              <nav className="tf-docs-toc" aria-label="On this page">
                <div className="tf-docs-toc-label">On this page</div>
                <ol>
                  {toc.map((item) => (
                    <li key={item.id} className={item.level === 3 ? "depth-2" : ""}>
                      <button type="button" onClick={() => scrollToHeading(item.id)}>
                        {item.text}
                      </button>
                    </li>
                  ))}
                </ol>
              </nav>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}