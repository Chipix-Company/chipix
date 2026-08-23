import {
  extractChangedSnippets,
  extractRecentSnippets,
  extractSvDeclarations,
} from "./svDeclarations";

export class ContextGatherer {
  constructor() {
    this._recentlyOpened = [];
  }

  noteFileOpened(fileId) {
    if (!fileId) return;
    this._recentlyOpened = [fileId, ...this._recentlyOpened.filter((id) => id !== fileId)].slice(0, 8);
  }

  gather({
    activeFile,
    ideFiles = [],
    revisionMap = {},
  }) {
    const filepath = activeFile?.path || activeFile?.name || "";
    const content = activeFile?.content || "";
    return {
      declarations: extractSvDeclarations(content, filepath),
      relevant_snippets_from_changed_files: extractChangedSnippets(
        ideFiles,
        activeFile?.id,
        revisionMap,
      ),
      relevant_snippets_from_recently_opened_files: extractRecentSnippets(
        ideFiles,
        activeFile?.id,
        this._recentlyOpened,
      ),
    };
  }
}
