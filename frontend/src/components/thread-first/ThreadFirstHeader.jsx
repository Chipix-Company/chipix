import React, { useEffect, useRef, useState } from "react";
import Icon from "./icons";

function projectInitials(name = "") {
  return String(name)
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() || "")
    .join("") || "·";
}

const GRADIENTS = [
  "linear-gradient(135deg,#c2a06e,#8a6a3e)",
  "linear-gradient(135deg,#7a8da0,#4a5c70)",
  "linear-gradient(135deg,#a07e7e,#705252)",
  "linear-gradient(135deg,#8aa07e,#52704e)",
  "linear-gradient(135deg,#a08e7e,#705a52)",
];
function gradientFor(id = "") {
  const idx = Math.abs(Array.from(String(id)).reduce((a, c) => a + c.charCodeAt(0), 0)) % GRADIENTS.length;
  return GRADIENTS[idx];
}

export default function ThreadFirstHeader({
  projects = [],
  activeProject,
  onSelectProject,
  onCreateProject,
  health = "ok",         // "ok" | "busy" | "bad"
  healthLabel,
  projectSubtitle,       // state-aware label (e.g. "designing now", "verifying", "ready to verify")
  onPalette,
  onToggleTheme,
  isDarkTheme,
  onOpenDrawer,
  onReset,
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  return (
    <header className="tf-header">
      <div className="tf-proj-switch" ref={menuRef}>
        <button className="tf-proj-btn" onClick={() => setMenuOpen((v) => !v)}>
          <div className="tf-proj-mark" style={{ background: gradientFor(activeProject?.id) }}>
            {projectInitials(activeProject?.name || "·")}
          </div>
          <div className="tf-proj-title">
            <div className="n">{activeProject?.name || "Select a project"}</div>
            <div className="s">
              {projectSubtitle || `${projects.length} project${projects.length === 1 ? "" : "s"}`}
            </div>
          </div>
          <Icon.ChevronDown width="14" height="14" stroke="var(--tf-ink-3)" />
        </button>
        <div className={`tf-proj-menu ${menuOpen ? "open" : ""}`}>
          {projects.map((p) => (
            <button
              key={p.id}
              className={`tf-item ${activeProject?.id === p.id ? "active" : ""}`}
              onClick={() => { setMenuOpen(false); onSelectProject?.(p.id); }}
            >
              <div className="tf-pm-mark" style={{ background: gradientFor(p.id) }}>
                {projectInitials(p.name)}
              </div>
              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {p.name || "Untitled project"}
              </span>
            </button>
          ))}
          {projects.length === 0 ? (
            <div style={{ padding: 12, color: "var(--tf-ink-3)", fontSize: 13 }}>No projects yet.</div>
          ) : null}
          <div className="tf-divider" />
          <button className="tf-new" onClick={() => { setMenuOpen(false); onCreateProject?.(); }}>
            <Icon.Plus width="14" height="14" />
            Start a new project
          </button>
        </div>
      </div>

      <div className="tf-spacer" />

      <button className="tf-pal-trigger" onClick={onPalette}>
        <Icon.Search width="14" height="14" />
        <span>Jump to anything</span>
        <span className="tf-kbd">⌘K</span>
      </button>

      <div className={`tf-health ${health === "bad" ? "bad" : health === "busy" ? "busy" : ""}`}>
        <span className="pulse" />
        {healthLabel || (health === "ok" ? "Everything healthy" : health === "busy" ? "Working" : "Backend issue")}
      </div>

      {onReset ? (
        <button className="tf-icon-btn" onClick={onReset} title="Reset conversation">
          <Icon.Refresh width="16" height="16" />
        </button>
      ) : null}
      <button className="tf-icon-btn" onClick={onToggleTheme} title="Switch theme">
        {isDarkTheme ? <Icon.Sun width="16" height="16" /> : <Icon.Moon width="16" height="16" />}
      </button>
      <button className="tf-icon-btn" onClick={onOpenDrawer} title="More">
        <Icon.Dots width="18" height="18" />
      </button>
    </header>
  );
}
