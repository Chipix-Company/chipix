/** Extract prefix/suffix windows around the cursor for FIM completion. */

const DEFAULT_LINES = 20;

export function extractPrefixSuffix(model, position, lineCount = DEFAULT_LINES) {
  if (!model || !position) {
    return { prefix: "", suffix: "", cursorLine: 1, cursorColumn: 1 };
  }

  const startLine = Math.max(1, position.lineNumber - lineCount);
  const endLine = Math.min(model.getLineCount(), position.lineNumber + lineCount);

  const prefixEnd = model.getValueInRange({
    startLineNumber: startLine,
    startColumn: 1,
    endLineNumber: position.lineNumber,
    endColumn: position.column,
  });

  const suffixStart = model.getValueInRange({
    startLineNumber: position.lineNumber,
    startColumn: position.column,
    endLineNumber: endLine,
    endColumn: model.getLineMaxColumn(endLine),
  });

  return {
    prefix: prefixEnd,
    suffix: suffixStart,
    cursorLine: position.lineNumber,
    cursorColumn: position.column,
  };
}

export function cacheKeyForRequest({ prefix, suffix, filepath, language, revisionKey }) {
  return [language || "", filepath || "", revisionKey || "", prefix || "", suffix || ""].join("\u0001");
}
