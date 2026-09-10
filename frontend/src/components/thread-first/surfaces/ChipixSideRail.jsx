/**
 * ChipixSideRail — the slim left-side icon rail.
 *
 * Why this exists (first principles): a desktop app is recognisable by
 * its persistent navigation. Slack, VS Code, Linear, Figma — they all
 * have a vertical strip of intent shortcuts on the left. Chipix has the
 * command palette (⌘K) for power users, but a new user opens the app
 * and feels lost without visible scaffolding.
 *
 * The rail is intentionally narrow (~56px). Each icon is the smallest
 * possible "I want X" target. Active state mirrors which overlay is
 * open, so the rail also functions as a "where am I?" beacon.
 *
 * Spacing is generous (12px between items) so the icons read as
 * distinct intents, not a toolbar.
 */

import React from "react";
import Icon from "../icons";
import { playTick } from "../useChipixSound";
import DesktopUpdateSideRail from "../../desktop/DesktopUpdateSideRail";

const TOP_ITEMS = [
  { id: "threads", label: "Threads", hint: "Conversation history", Ico: Icon.History },
  { id: "ide",     label: "Editor",  hint: "Open the workspace IDE", Ico: Icon.Code },
  { id: "mental",  label: "Mental model", hint: "How I read your design", Ico: Icon.Layers },
  { id: "codebase", label: "Codebase map", hint: "Structural RTL + spec graph", Ico: Icon.Graph },
  { id: "staged",  label: "Staged flow", hint: "Prepare, plan, and run verification", Ico: Icon.Play },
  { id: "tasks",   label: "Task board", hint: "Agent tasks and verification work", Ico: Icon.Board },
  { id: "dash",    label: "Dashboard", hint: "Runs, KPIs, failures", Ico: Icon.Activity },
];

const BOTTOM_ITEMS = [
  { id: "founders", label: "Letter from the founders", hint: "Help us shape ChipVerify", Ico: Icon.Mail },
  { id: "theme",    label: "Theme", hint: "Switch light / dark" },
  { id: "settings", label: "Settings", hint: "More options", Ico: Icon.Settings },
];

export default function ChipixSideRail({
  active = null,        // "threads" | "ide" | "mental" | "dash" | null
  isDarkTheme = false,
  onSelect,             // (id) => void
  onToggleTheme,
  onOpenProjectSwitch,  // Cx mark → open palette (or project menu)
}) {
  const fire = (id) => {
    playTick({ pitch: id === "settings" ? "low" : "high" });
    onSelect?.(id);
  };

  return (
    <nav className="tf-side-rail" data-tour="side-rail" aria-label="Workspace shortcuts">
      <button
        type="button"
        className="tf-side-rail-mark"
        data-tour="side-rail-mark"
        onClick={() => { playTick({ pitch: "low" }); onOpenProjectSwitch?.(); }}
        aria-label="Switch project or jump to anything"
        title="Switch project · ⌘K"
      >
        <img
          className="tf-side-rail-mark-img"
          src="/brand/chipix-app-icon.png"
          alt=""
          width={32}
          height={32}
          draggable={false}
        />
      </button>

      <div className="tf-side-rail-group" role="toolbar" aria-label="Main shortcuts">
        {TOP_ITEMS.map((it) => {
          const Ico = it.Ico;
          const isActive = active === it.id;
          return (
            <button
              key={it.id}
              type="button"
              className={`tf-side-rail-btn${isActive ? " active" : ""}`}
              data-tour={`side-rail-${it.id}`}
              onClick={() => fire(it.id)}
              aria-label={it.label}
              aria-pressed={isActive}
              data-tip={it.hint}
            >
              <span className="tf-side-rail-ind" aria-hidden />
              <Ico width="18" height="18" />
              <span className="tf-side-rail-tip">{it.label}</span>
            </button>
          );
        })}
      </div>

      <div className="tf-side-rail-spacer" />

      <div className="tf-side-rail-group bottom" role="toolbar" aria-label="App settings">
        {BOTTOM_ITEMS.filter((b) => b.id === "founders").map((it) => {
          const Ico = it.Ico;
          const isActive = active === it.id;
          return (
            <button
              key={it.id}
              type="button"
              className={`tf-side-rail-btn${isActive ? " active" : ""}`}
              data-tour="side-rail-founders"
              onClick={() => fire(it.id)}
              aria-label={it.label}
              aria-pressed={isActive}
              data-tip={it.hint}
            >
              <span className="tf-side-rail-ind" aria-hidden />
              <Ico width="18" height="18" />
              <span className="tf-side-rail-tip">{it.label}</span>
            </button>
          );
        })}
        <button
          type="button"
          className="tf-side-rail-btn"
          onClick={() => { playTick({ pitch: "low" }); onToggleTheme?.(); }}
          aria-label={isDarkTheme ? "Switch to light theme" : "Switch to dark theme"}
        >
          {isDarkTheme ? <Icon.Sun width="18" height="18" /> : <Icon.Moon width="18" height="18" />}
          <span className="tf-side-rail-tip">Theme</span>
        </button>
        <DesktopUpdateSideRail />
        {BOTTOM_ITEMS.filter((b) => b.id === "settings").map((it) => {
          const Ico = it.Ico;
          const isActive = active === it.id;
          return (
            <button
              key={it.id}
              type="button"
              className={`tf-side-rail-btn${isActive ? " active" : ""}`}
              onClick={() => fire(it.id)}
              aria-label={it.label}
              aria-pressed={isActive}
            >
              <Ico width="18" height="18" />
              <span className="tf-side-rail-tip">{it.label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
