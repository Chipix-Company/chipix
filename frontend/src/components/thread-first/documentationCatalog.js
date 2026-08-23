/**
 * User-facing documentation catalog — strict allowlist only.
 * In-app Documentation shows these guides and nothing else.
 * Keep in sync with backend/user_documentation.py
 */

export const DOC_SECTIONS = [
  {
    id: "start",
    label: "Start here",
    items: [
      { name: "getting-started.md", title: "Getting started", glyph: "✦" },
      { name: "workspace-layout.md", title: "Workspace layout", glyph: "▦" },
      { name: "the-thread.md", title: "The conversation thread", glyph: "💬" },
    ],
  },
  {
    id: "workflows",
    label: "Workflows",
    items: [
      { name: "designing-rtl.md", title: "Designing RTL", glyph: "◇" },
      { name: "running-verification.md", title: "Running verification", glyph: "▶" },
      { name: "understanding-results.md", title: "Understanding results", glyph: "◎" },
      { name: "patches-and-diffs.md", title: "Patches & diffs", glyph: "±" },
      { name: "task-board.md", title: "Task board", glyph: "▤" },
    ],
  },
  {
    id: "reference",
    label: "Reference",
    items: [
      { name: "files-and-editor.md", title: "Files & editor", glyph: "📄" },
      { name: "keyboard-shortcuts.md", title: "Keyboard shortcuts", glyph: "⌘" },
      { name: "glossary.md", title: "Glossary", glyph: "Aa" },
    ],
  },
  {
    id: "help",
    label: "Help",
    items: [
      { name: "troubleshooting.md", title: "Troubleshooting", glyph: "?" },
    ],
  },
];

const CATALOG_BY_NAME = new Map();
/** @type {Set<string>} */
export const ALLOWED_DOC_NAMES = new Set();

for (const section of DOC_SECTIONS) {
  for (const item of section.items) {
    CATALOG_BY_NAME.set(item.name, { ...item, sectionId: section.id, sectionLabel: section.label });
    ALLOWED_DOC_NAMES.add(item.name);
  }
}

/** True only for curated consumer guides in the catalog allowlist. */
export function isUserFacingDoc(entry) {
  const name = String(entry?.name || "");
  return name.endsWith(".md") && ALLOWED_DOC_NAMES.has(name);
}

export function isCatalogDocName(name) {
  return ALLOWED_DOC_NAMES.has(String(name || ""));
}

export function enrichDocEntry(entry) {
  const cat = CATALOG_BY_NAME.get(entry?.name);
  if (!cat) {
    return {
      ...entry,
      displayTitle: entry?.title || entry?.name || "Document",
      glyph: "·",
      sectionId: "other",
      sectionLabel: "More",
    };
  }
  return {
    ...entry,
    displayTitle: cat.title,
    glyph: cat.glyph,
    sectionId: cat.sectionId,
    sectionLabel: cat.sectionLabel,
  };
}

export function groupDocsForSidebar(docs) {
  const enriched = docs.filter(isUserFacingDoc).map(enrichDocEntry);
  const byName = new Map(enriched.map((d) => [d.name, d]));

  return DOC_SECTIONS.map((section) => ({
    id: section.id,
    label: section.label,
    items: section.items
      .map((item) => byName.get(item.name))
      .filter(Boolean),
  })).filter((s) => s.items.length > 0);
}

export function defaultDocName(docs) {
  const names = new Set(docs.filter(isUserFacingDoc).map((d) => d.name));
  if (names.has("getting-started.md")) return "getting-started.md";
  for (const section of DOC_SECTIONS) {
    for (const item of section.items) {
      if (names.has(item.name)) return item.name;
    }
  }
  return "";
}
