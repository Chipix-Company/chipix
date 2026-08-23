/**
 * Chipix workspace shell tour — highlights persistent chrome only (no overlays).
 * Targets use [data-tour] attributes on side rail, title bar, mode toggle, composer.
 */

export const CHIPIX_SHELL_STEPS = [
  {
    target: '[data-tour="thread-area"]',
    title: "Welcome to Chipix",
    content:
      "Chip-Verify is a thread-first workspace: you describe what you want in plain language, and the agent plans, edits RTL, and runs verification from this conversation.",
    placement: "center",
    skipBeacon: true,
  },
  {
    target: '[data-tour="side-rail-mark"]',
    title: "Projects & quick jump",
    content:
      "Open the Cx menu to switch projects or press ⌘K (Ctrl+K) for the command palette — jump to files, runs, docs, or actions without hunting menus.",
    placement: "right",
  },
  {
    target: '[data-tour="side-rail-threads"]',
    title: "Conversation history",
    content:
      "Every chat thread for this project lives here. Open it to switch conversations, start a new one, or review past work.",
    placement: "right",
  },
  {
    target: '[data-tour="side-rail-ide"]',
    title: "Editor workspace",
    content:
      "Open the IDE to browse RTL and specs, view diffs, and approve patches the agent proposes.",
    placement: "right",
  },
  {
    target: '[data-tour="side-rail-mental"]',
    title: "Mental model",
    content:
      "See how the system understands your design — interfaces, hierarchy, and assumptions — before you run heavy verification.",
    placement: "right",
  },
  {
    target: '[data-tour="side-rail-staged"]',
    title: "Staged verification",
    content:
      "Run verification in controlled stages: prepare context, review the plan, then execute. Safer than firing everything at once.",
    placement: "right",
  },
  {
    target: '[data-tour="side-rail-dash"]',
    title: "Dashboard",
    content:
      "Track verification runs, KPIs, and failures for this project in one place.",
    placement: "right",
  },
  {
    target: '[data-tour="titlebar-project"]',
    title: "Active project",
    content:
      "Your current project name and metadata live here. Use this menu to switch projects or create a new one.",
    placement: "bottom",
  },
  {
    target: '[data-tour="mode-toggle"]',
    title: "Design vs Verify",
    content:
      "Choose your focus lens: Design shapes suggestions and RTL work; Verify nudges you toward checks and staged runs (may gate until the mental model is ready).",
    placement: "top",
  },
  {
    target: '[data-tour="composer"]',
    title: "Start here",
    content:
      "Describe what to build, attach specs or RTL, or tap a starter chip. This is how every workflow begins.",
    placement: "top",
  },
];
