import React, { useEffect, useMemo, useRef, useState } from "react";
import Icon from "../icons";

function isShortcutMeta(meta) {
  const m = String(meta || "").trim();
  if (!m) return false;
  if (m.length > 14) return false;
  return /⌘|Ctrl|⇧|Shift|Alt|Esc|↑|↓|↵|Enter/i.test(m);
}

function ItemMeta({ meta }) {
  if (!meta) return null;
  const text = String(meta).trim();
  if (!text) return null;
  if (isShortcutMeta(text)) {
    return (
      <span className="meta">
        <span className="tf-kbd">{text}</span>
      </span>
    );
  }
  return <span className="hint">{text}</span>;
}

export default function CommandPalette({ open, onClose, sections = [], onPick }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sections;
    return sections
      .map((s) => ({
        ...s,
        items: (s.items || []).filter((it) => {
          const hay = `${it.name || ""} ${it.meta || ""} ${it.hint || ""}`.toLowerCase();
          return hay.includes(q);
        }),
      }))
      .filter((s) => s.items.length > 0);
  }, [query, sections]);

  const flatItems = useMemo(() => filtered.flatMap((s) => s.items), [filtered]);

  useEffect(() => {
    if (active >= flatItems.length) setActive(Math.max(0, flatItems.length - 1));
  }, [active, flatItems.length]);

  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector(".tf-pal-item.active");
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [active, open, filtered]);

  if (!open) return null;

  const handleKey = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose?.();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(flatItems.length - 1, i + 1));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const picked = flatItems[active];
      if (picked) {
        onPick?.(picked);
        onClose?.();
      }
    }
  };

  return (
    <div
      className="tf-pal-overlay open"
      role="presentation"
      onClick={() => onClose?.()}
    >
      <div
        className="tf-palette"
        role="dialog"
        aria-modal="true"
        aria-label="Jump to anything"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="tf-pal-input">
          <span className="tf-pal-input-icon" aria-hidden>
            <Icon.Search width="17" height="17" />
          </span>
          <input
            ref={inputRef}
            type="search"
            placeholder="Jump to anything — files, runs, projects, actions"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKey}
            autoComplete="off"
            spellCheck={false}
            aria-label="Search commands and destinations"
          />
          {query ? (
            <button
              type="button"
              className="tf-pal-clear"
              aria-label="Clear search"
              onClick={() => {
                setQuery("");
                inputRef.current?.focus();
              }}
            >
              <Icon.X width="14" height="14" />
            </button>
          ) : null}
        </div>

        <div className="tf-pal-list" ref={listRef}>
          {filtered.length === 0 ? (
            <div className="tf-pal-empty">
              <span className="tf-pal-empty-icon" aria-hidden>
                <Icon.Search width="22" height="22" />
              </span>
              <p>No matches for &ldquo;{query.trim()}&rdquo;</p>
              <span className="tf-pal-empty-sub">
                Try a file name, run status, or action like &ldquo;verify&rdquo;
              </span>
            </div>
          ) : (
            filtered.map((section, sectionIdx) => (
              <div key={section.label} className="tf-pal-group">
                <div className="tf-pal-section">
                  <span className="tf-pal-section-label">{section.label}</span>
                  <span className="tf-pal-section-n">{section.items.length}</span>
                </div>
                {section.items.map((it) => {
                  const idx = flatItems.indexOf(it);
                  const isActive = idx === active;
                  return (
                    <button
                      key={it.id}
                      type="button"
                      className={`tf-pal-item${isActive ? " active" : ""}`}
                      onMouseEnter={() => setActive(idx)}
                      onClick={() => {
                        onPick?.(it);
                        onClose?.();
                      }}
                    >
                      <span className="glyph" aria-hidden>
                        {it.glyph || section.glyph || "·"}
                      </span>
                      <span className="copy">
                        <span className="name">{it.name}</span>
                        {it.hint ? <span className="hint">{it.hint}</span> : null}
                      </span>
                      <ItemMeta meta={it.meta} />
                    </button>
                  );
                })}
                {sectionIdx < filtered.length - 1 ? (
                  <div className="tf-pal-group-divider" aria-hidden />
                ) : null}
              </div>
            ))
          )}
        </div>

        <div className="tf-pal-foot">
          <div className="tf-pal-foot-hints">
            <span className="tf-pal-foot-hint">
              <span className="tf-kbd">↑↓</span> navigate
            </span>
            <span className="tf-pal-foot-hint">
              <span className="tf-kbd">↵</span> open
            </span>
            <span className="tf-pal-foot-hint">
              <span className="tf-kbd">Esc</span> close
            </span>
          </div>
          {flatItems.length > 0 ? (
            <span className="tf-pal-count">
              {flatItems.length} {flatItems.length === 1 ? "result" : "results"}
            </span>
          ) : null}
        </div>
      </div>
    </div>
  );
}
