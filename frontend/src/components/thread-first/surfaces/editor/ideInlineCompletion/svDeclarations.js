/** Lightweight SV declaration extraction from open buffers. */

const MODULE_RE = /^\s*(module|interface|package|program)\s+(\w+)/gim;
const PORT_RE = /^\s*(input|output|inout)\s+[^;]+/gim;

export function extractSvDeclarations(content = "", filepath = "") {
  const text = String(content || "");
  if (!text.trim()) return [];

  const lines = text.split(/\r?\n/);
  const headerLines = [];
  let inModule = false;

  for (const line of lines.slice(0, 80)) {
    if (MODULE_RE.test(line)) {
      MODULE_RE.lastIndex = 0;
      inModule = true;
      headerLines.push(line);
      continue;
    }
    MODULE_RE.lastIndex = 0;
    if (!inModule) continue;
    headerLines.push(line);
    if (line.includes(");")) break;
  }

  const body = headerLines.join("\n").trim();
  if (!body) return [];

  return [{
    filepath,
    body: body.slice(0, 600),
    kind: "declaration",
    source: "client_buffer",
  }];
}

export function extractChangedSnippets(files = [], activeFileId, revisionMap = {}) {
  const snippets = [];
  for (const file of files) {
    if (!file?.id || file.id === activeFileId) continue;
    const rev = revisionMap[file.id];
    if (!rev || !file.content) continue;
    snippets.push({
      filepath: file.path || file.name,
      body: String(file.content).slice(0, 400),
      kind: "changed",
      source: "client_session",
    });
  }
  return snippets.slice(0, 4);
}

export function extractRecentSnippets(files = [], activeFileId, recentlyOpened = []) {
  const snippets = [];
  const order = recentlyOpened.length ? recentlyOpened : files.map((f) => f.id);
  for (const id of order) {
    if (id === activeFileId) continue;
    const file = files.find((f) => f.id === id);
    if (!file?.content) continue;
    snippets.push({
      filepath: file.path || file.name,
      body: String(file.content).slice(0, 300),
      kind: "recent",
      source: "client_recent",
    });
    if (snippets.length >= 3) break;
  }
  return snippets;
}
