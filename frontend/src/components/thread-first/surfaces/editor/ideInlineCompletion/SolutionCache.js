/** LRU cache + Tabby-style forwarding cache for prefix-stable ghost text. */

export class SolutionCache {
  constructor({ maxEntries = 100, ttlMs = 5 * 60 * 1000 } = {}) {
    this.maxEntries = maxEntries;
    this.ttlMs = ttlMs;
    this._entries = new Map();
    /** @type {Map<string, { insertText: string, prefix: string, ts: number }>} */
    this._forwardByFile = new Map();
  }

  get(key) {
    const row = this._entries.get(key);
    if (!row) return null;
    if (Date.now() - row.ts > this.ttlMs) {
      this._entries.delete(key);
      return null;
    }
    this._entries.delete(key);
    this._entries.set(key, row);
    return row.value;
  }

  set(key, value, meta = {}) {
    if (this._entries.has(key)) {
      this._entries.delete(key);
    }
    this._entries.set(key, { value, ts: Date.now() });
    while (this._entries.size > this.maxEntries) {
      const oldest = this._entries.keys().next().value;
      this._entries.delete(oldest);
    }

    const filepath = meta.filepath;
    const prefix = meta.prefix;
    const insertText = value?.insertText;
    if (filepath && prefix != null && insertText) {
      this._forwardByFile.set(filepath, {
        insertText,
        prefix,
        ts: Date.now(),
      });
    }
  }

  /**
   * Tabby preCache/postCache: reuse suggestion when user typed along its prefix.
   */
  tryForward({ prefix, suffix, filepath, language, revisionKey }) {
    if (!filepath) return null;
    const row = this._forwardByFile.get(filepath);
    if (!row || Date.now() - row.ts > this.ttlMs) return null;

    const cachedPrefix = row.prefix || "";
    const fullInsert = row.insertText || "";
    if (!fullInsert) return null;

    // User continued typing: new prefix = old prefix + typed chars matching suggestion start
    if (!String(prefix).startsWith(cachedPrefix)) return null;

    const typedAhead = String(prefix).slice(cachedPrefix.length);
    if (!fullInsert.startsWith(typedAhead)) return null;

    const remaining = fullInsert.slice(typedAhead.length);
    if (!remaining.trim()) return null;

    const forwardKey = [language || "", filepath, revisionKey || "", prefix, suffix].join("\u0001");
    const item = {
      insertText: remaining,
      completionId: row.completionId,
      filepath,
      language,
    };
    this.set(forwardKey, item, { filepath, prefix });
    return item;
  }

  invalidateForFile(filepath) {
    if (!filepath) return;
    this._forwardByFile.delete(filepath);
    for (const key of [...this._entries.keys()]) {
      if (key.includes(filepath)) {
        this._entries.delete(key);
      }
    }
  }

  clear() {
    this._entries.clear();
    this._forwardByFile.clear();
  }
}
