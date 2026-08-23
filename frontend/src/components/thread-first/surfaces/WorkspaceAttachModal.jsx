import React, { useMemo, useState, useEffect, useRef } from "react";
import { artifactKindFromRecord, kindLabel } from "../artifactAttach";

const MAX_ATTACH = 4;

function artifactLabel(a) {
  if (!a) return "";
  return (
    String(a.memberPath || a.filename || a.metadata?.relative_path || a.metadata?.path || a.name || "")
    || String(a.id || "").slice(0, 8)
  );
}

export function artifactKey(a) {
  const id = a?.artifactId || a?.id || "";
  const mp = a?.memberPath ? String(a.memberPath) : "";
  return `${id}::${mp}`;
}

export function attachmentRowKey(row) {
  if (!row?.artifactId) return "";
  const mp = row.memberPath ? String(row.memberPath) : "";
  return `${row.artifactId}::${mp}`;
}

function groupArtifacts(list) {
  const spec = [];
  const rtl = [];
  const other = [];
  (list || []).forEach((a) => {
    const kind = artifactKindFromRecord(a);
    if (kind === "spec") spec.push(a);
    else if (kind === "rtl") rtl.push(a);
    else other.push(a);
  });
  return { spec, rtl, other };
}

/**
 * Modal to pick existing project artifacts as chat context (names + optional content fetch by parent).
 */
export default function WorkspaceAttachModal({
  open,
  onClose,
  artifacts = [],
  existingKeys = [],
  onConfirm,
}) {
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState(() => new Set());
  const inputRef = useRef(null);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setPicked(new Set());
      return;
    }
    const t = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(t);
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = Array.isArray(artifacts) ? artifacts : [];
    if (!q) return list;
    return list.filter((a) => {
      const label = artifactLabel(a).toLowerCase();
      const type = String(a.artifact_type || "").toLowerCase();
      return label.includes(q) || type.includes(q) || String(a.id || "").toLowerCase().includes(q);
    });
  }, [artifacts, query]);

  const grouped = useMemo(() => groupArtifacts(filtered), [filtered]);

  const existingSet = useMemo(() => new Set(existingKeys), [existingKeys]);

  const toggle = (a) => {
    const k = artifactKey(a);
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(k)) {
        next.delete(k);
        return next;
      }
      if (next.size + existingSet.size >= MAX_ATTACH) return prev;
      next.add(k);
      return next;
    });
  };

  if (!open) return null;

  const atCap = picked.size + existingSet.size >= MAX_ATTACH;

  const submit = () => {
    const keyToArt = new Map((artifacts || []).map((a) => [artifactKey(a), a]));
    const chosen = [...picked].map((k) => keyToArt.get(k)).filter(Boolean);
    const rows = chosen.map((a) => ({
      artifactId: a.artifactId || a.id,
      memberPath: a.memberPath || undefined,
      label: artifactLabel(a),
      artifactKind: artifactKindFromRecord(a),
    }));
    onConfirm?.(rows);
    onClose?.();
  };

  const renderRow = (a) => {
    const k = artifactKey(a);
    const isExisting = existingSet.has(k);
    const isPicked = picked.has(k);
    const checked = isExisting || isPicked;
    const disableInput = isExisting || (!isPicked && atCap);
    const typeLabel = kindLabel(artifactKindFromRecord(a));
    return (
      <li key={k}>
        <label className={`tf-wsattach-row${disableInput && !isPicked ? " disabled" : ""}`}>
          <input
            type="checkbox"
            checked={checked}
            disabled={disableInput}
            onChange={() => {
              if (isExisting) return;
              toggle(a);
            }}
          />
          <span className={`tf-wsattach-type kind-${artifactKindFromRecord(a)}`}>{typeLabel}</span>
          <span className="tf-wsattach-name">{artifactLabel(a)}</span>
          {isExisting ? (
            <span className="tf-wsattach-meta">in composer</span>
          ) : null}
        </label>
      </li>
    );
  };

  const renderSection = (title, items) => {
    if (!items.length) return null;
    return (
      <>
        <li className="tf-wsattach-section" aria-hidden>{title}</li>
        {items.map(renderRow)}
      </>
    );
  };

  const hasAny = grouped.spec.length + grouped.rtl.length + grouped.other.length > 0;

  return (
    <div className="tf-wsattach-overlay" role="dialog" aria-modal="true" aria-label="Attach workspace files">
      <button type="button" className="tf-wsattach-backdrop" aria-label="Close" onClick={onClose} />
      <div className="tf-wsattach-panel">
        <div className="tf-wsattach-head">
          <h3 className="tf-wsattach-title">Attach from workspace</h3>
          <p className="tf-wsattach-sub">
            Pick specification or RTL files for the next message (up to {MAX_ATTACH} total, including chips already in the composer).
          </p>
          <input
            ref={inputRef}
            type="search"
            className="tf-wsattach-search"
            placeholder="Filter by file name…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <ul className="tf-wsattach-list">
          {!hasAny ? (
            <li className="tf-wsattach-empty">
              No project files yet. Use the paperclip in the composer to upload a specification or RTL first.
            </li>
          ) : (
            <>
              {renderSection("Specification", grouped.spec)}
              {renderSection("RTL", grouped.rtl)}
              {renderSection("Other", grouped.other)}
            </>
          )}
        </ul>
        <div className="tf-wsattach-actions">
          <button type="button" className="tf-btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="tf-btn primary"
            onClick={submit}
            disabled={picked.size === 0}
          >
            Add {picked.size ? `(${picked.size})` : ""}
          </button>
        </div>
      </div>
    </div>
  );
}

export { MAX_ATTACH };
