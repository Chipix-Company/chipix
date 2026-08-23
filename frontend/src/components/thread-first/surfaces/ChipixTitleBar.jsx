/**
 * ChipixTitleBar — the single, unified top bar.
 *
 * Replaces the old ThreadFirstHeader, which had become a duplicate of the
 * side rail and status bar. The title bar now owns three things and three
 * things only:
 *
 *   1. **Identity.**     Cx mark + project switcher.
 *   2. **Location.**     Contextual breadcrumb — "Conversation",
 *                        "Mental Model · uart_top", "Editor — top.sv".
 *                        With a back/close affordance when an overlay is open.
 *   3. **Native chrome.** Drag region for moving the window + window control
 *                        buttons (— □ ×). Platform-aware: macOS leaves a
 *                        gutter for traffic-light buttons; Windows draws
 *                        the controls itself.
 *
 * Everything else moved out:
 *   • Health pill → ChipixStatusBar (the strip exists for ambient state)
 *   • Theme toggle → ChipixSideRail (rail owns app settings)
 *   • Drawer ⋮ → ChipixSideRail (Threads icon)
 *   • Reset / new chat → project switcher menu
 *
 * Drag region pattern: the bar itself is `-webkit-app-region: drag` via
 * the `tf-titlebar` class. Every interactive island sets
 * `WebkitAppRegion: 'no-drag'` inline so clicks work.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Icon from "../icons";
import { playTick } from "../useChipixSound";
import {
  filterProjects,
  gradientForProject,
  projectInitials,
  projectMetaLine,
} from "../projectPickerUtils";

const NO_DRAG = { WebkitAppRegion: "no-drag" };

function useElectronWindow() {
  const api = typeof window !== "undefined" ? window.electronAPI : null;
  const ctrl = api?.window;
  const [isMax, setIsMax] = useState(false);
  const [available] = useState(Boolean(ctrl));
  const platform = api?.platform || (typeof navigator !== "undefined" && /mac/i.test(navigator.platform) ? "darwin" : "win32");

  useEffect(() => {
    if (!ctrl) return undefined;
    let cancelled = false;
    Promise.resolve(ctrl.isMaximized?.()).then((v) => { if (!cancelled) setIsMax(Boolean(v)); }).catch(() => {});
    const unsub = ctrl.onMaximizeState?.((v) => setIsMax(Boolean(v)));
    return () => { cancelled = true; if (typeof unsub === "function") unsub(); };
  }, [ctrl]);

  return {
    available,
    isMax,
    platform,
    minimize:  () => ctrl?.minimize?.(),
    toggleMax: () => ctrl?.toggleMaximize?.(),
    close:     () => ctrl?.close?.(),
  };
}

export default function ChipixTitleBar({
  projects = [],
  activeProject = null,
  onSelectProject,
  onCreateProject,
  onResetThread,
  // Overlay context (for the breadcrumb / back affordance)
  overlay = null,            // null | { id, label, sub, onClose }
  // Palette
  onOpenPalette,
}) {
  const win = useElectronWindow();
  const isMac = win.platform === "darwin";
  const [menuOpen, setMenuOpen] = useState(false);
  const [search, setSearch] = useState("");
  const menuRef = useRef(null);
  const searchRef = useRef(null);

  const filteredProjects = useMemo(
    () => filterProjects(projects, search),
    [projects, search],
  );

  const closeMenu = useCallback(() => {
    setMenuOpen(false);
    setSearch("");
  }, []);

  // Close project menu on outside click.
  useEffect(() => {
    if (!menuOpen) return undefined;
    const fn = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        closeMenu();
      }
    };
    document.addEventListener("mousedown", fn);
    return () => document.removeEventListener("mousedown", fn);
  }, [menuOpen, closeMenu]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const fn = (e) => {
      if (e.key === "Escape") closeMenu();
    };
    document.addEventListener("keydown", fn);
    return () => document.removeEventListener("keydown", fn);
  }, [menuOpen, closeMenu]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const t = requestAnimationFrame(() => searchRef.current?.focus());
    return () => cancelAnimationFrame(t);
  }, [menuOpen]);

  const handleMin   = useCallback(() => { playTick({ pitch: "low" }); win.minimize(); }, [win]);
  const handleMax   = useCallback(() => { playTick({ pitch: "high" }); win.toggleMax(); }, [win]);
  const handleClose = useCallback(() => { playTick({ pitch: "low" }); win.close(); }, [win]);

  const handleBack = useCallback(() => {
    if (!overlay?.onClose) return;
    playTick({ pitch: "low" });
    overlay.onClose();
  }, [overlay]);

  const openMenu = useCallback(() => {
    playTick({ pitch: "low" });
    setMenuOpen((v) => !v);
  }, []);

  return (
    <header
      className={`tf-titlebar${isMac ? " mac" : ""}${overlay ? " in-overlay" : ""}`}
      role="banner"
    >
      {/* Identity — Cx mark + project switcher */}
      <div className="tf-tb-identity" ref={menuRef} style={NO_DRAG}>
        <button
          type="button"
          className={`tf-tb-proj-btn${menuOpen ? " open" : ""}`}
          data-tour="titlebar-project"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={openMenu}
          title="Switch project"
        >
          <span
            className="tf-tb-mark"
            style={{ background: gradientForProject(activeProject?.id) }}
            aria-hidden
          >
            <span className="tf-tb-mark-inner">{projectInitials(activeProject?.name)}</span>
          </span>
          <span className="tf-tb-proj-text">
            <span className="tf-tb-proj-name">
              {activeProject?.name || "Select a project"}
            </span>
            {activeProject ? (
              <span className="tf-tb-proj-meta">{projectMetaLine(activeProject)}</span>
            ) : null}
          </span>
          <span className="tf-tb-proj-chevron" aria-hidden>
            <Icon.ChevronDown width="12" height="12" />
          </span>
        </button>

        {menuOpen ? (
          <div className="tf-tb-menu" role="menu" aria-label="Projects">
            <div className="tf-tb-menu-head">
              <span className="tf-tb-menu-title">Projects</span>
              <span className="tf-tb-menu-count">{projects.length}</span>
            </div>

            {projects.length > 1 ? (
              <label className="tf-tb-menu-search">
                <Icon.Search width="14" height="14" aria-hidden />
                <input
                  ref={searchRef}
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search projects…"
                  aria-label="Search projects"
                  autoComplete="off"
                  spellCheck={false}
                />
                {search ? (
                  <button
                    type="button"
                    className="tf-tb-menu-search-clear"
                    onClick={() => setSearch("")}
                    aria-label="Clear search"
                  >
                    <Icon.Close width="12" height="12" />
                  </button>
                ) : null}
              </label>
            ) : null}

            <div className="tf-tb-menu-list" role="group" aria-label="Project list">
              {projects.length === 0 ? (
                <div className="tf-tb-menu-empty">No projects yet — create one below.</div>
              ) : filteredProjects.length === 0 ? (
                <div className="tf-tb-menu-empty">No projects match &ldquo;{search}&rdquo;</div>
              ) : (
                filteredProjects.map((p) => {
                  const active = activeProject?.id === p.id;
                  const meta = projectMetaLine(p);
                  return (
                    <button
                      key={p.id}
                      type="button"
                      role="menuitemradio"
                      aria-checked={active}
                      className={`tf-tb-menu-item${active ? " active" : ""}`}
                      onClick={() => {
                        closeMenu();
                        playTick({ pitch: "high" });
                        onSelectProject?.(p.id);
                      }}
                    >
                      <span
                        className="tf-tb-menu-mark"
                        style={{ background: gradientForProject(p.id) }}
                        aria-hidden
                      >
                        {projectInitials(p.name)}
                      </span>
                      <span className="tf-tb-menu-copy">
                        <span className="tf-tb-menu-name">{p.name || "Untitled project"}</span>
                        {meta ? <span className="tf-tb-menu-meta">{meta}</span> : null}
                      </span>
                      <span className="tf-tb-menu-tick" aria-hidden>
                        {active ? <Icon.Check width="12" height="12" /> : null}
                      </span>
                    </button>
                  );
                })
              )}
            </div>

            <div className="tf-tb-menu-foot">
              <button
                type="button"
                role="menuitem"
                className="tf-tb-menu-action"
                onClick={() => { closeMenu(); onCreateProject?.(); }}
              >
                <Icon.Plus width="14" height="14" />
                <span>New project</span>
              </button>
              {onResetThread ? (
                <button
                  type="button"
                  role="menuitem"
                  className="tf-tb-menu-action subtle"
                  onClick={() => { closeMenu(); onResetThread(); }}
                >
                  <Icon.Refresh width="14" height="14" />
                  <span>New conversation</span>
                </button>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>

      {/* Breadcrumb / location */}
      <div className="tf-tb-crumb" style={NO_DRAG}>
        {overlay ? (
          <>
            <button
              type="button"
              className="tf-tb-back"
              onClick={handleBack}
              aria-label="Back to conversation"
              title="Back to conversation (Esc)"
            >
              <Icon.ArrowLeft width="13" height="13" />
              <span>Back</span>
            </button>
            <span className="tf-tb-crumb-sep" aria-hidden>/</span>
            <span className="tf-tb-crumb-curr">
              {overlay.label}
              {overlay.sub ? <span className="tf-tb-crumb-sub"> · {overlay.sub}</span> : null}
            </span>
          </>
        ) : (
          <span className="tf-tb-crumb-curr passive">Conversation</span>
        )}
      </div>

      {/* Right cluster — palette + native window controls */}
      <div className="tf-tb-right" style={NO_DRAG}>
        <button
          type="button"
          className="tf-tb-palette"
          onClick={onOpenPalette}
          title="Jump to anything"
        >
          <Icon.Search width="13" height="13" />
          <span>Jump to anything</span>
          <span className="tf-kbd">⌘K</span>
        </button>

        {!isMac && win.available ? (
          <div className="tf-tb-wctl" aria-label="Window controls">
            <button type="button" className="tf-tb-wctl-btn min" onClick={handleMin} aria-label="Minimise">
              <svg viewBox="0 0 10 10" width="10" height="10" aria-hidden>
                <line x1="1.5" y1="5" x2="8.5" y2="5" stroke="currentColor" strokeWidth="1.1" />
              </svg>
            </button>
            <button type="button" className="tf-tb-wctl-btn max" onClick={handleMax}
                    aria-label={win.isMax ? "Restore" : "Maximise"}>
              {win.isMax ? (
                <svg viewBox="0 0 10 10" width="10" height="10" aria-hidden>
                  <rect x="2" y="1" width="6" height="6" fill="none" stroke="currentColor" strokeWidth="1.1" />
                  <rect x="1" y="3" width="6" height="6" fill="none" stroke="currentColor" strokeWidth="1.1" />
                </svg>
              ) : (
                <svg viewBox="0 0 10 10" width="10" height="10" aria-hidden>
                  <rect x="1.5" y="1.5" width="7" height="7" fill="none" stroke="currentColor" strokeWidth="1.1" />
                </svg>
              )}
            </button>
            <button type="button" className="tf-tb-wctl-btn close" onClick={handleClose} aria-label="Close">
              <svg viewBox="0 0 10 10" width="10" height="10" aria-hidden>
                <line x1="1.5" y1="1.5" x2="8.5" y2="8.5" stroke="currentColor" strokeWidth="1.2" />
                <line x1="8.5" y1="1.5" x2="1.5" y2="8.5" stroke="currentColor" strokeWidth="1.2" />
              </svg>
            </button>
          </div>
        ) : null}
      </div>
    </header>
  );
}
