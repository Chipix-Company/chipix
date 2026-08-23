function levenshtein(a, b) {
  const m = a.length;
  const n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;

  let prev = new Array(n + 1);
  let curr = new Array(n + 1);

  for (let j = 0; j <= n; j++) {
    prev[j] = j;
  }

  for (let i = 1; i <= m; i++) {
    curr[0] = i;
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      curr[j] = Math.min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost);
    }
    [prev, curr] = [curr, prev];
  }

  return prev[n];
}

export function fuzzyApplyRange(documentText, snippet) {
  const lines = documentText.split("\n");
  const snippetLines = snippet.split("\n");

  if (snippetLines.length === 0 || lines.length === 0) {
    return null;
  }

  if (snippetLines.length > lines.length) {
    return null;
  }

  let minDistance = Number.MAX_SAFE_INTEGER;
  let index = 0;

  for (let i = 0; i <= lines.length - snippetLines.length; i++) {
    const window = lines.slice(i, i + snippetLines.length).join("\n");
    const distance = levenshtein(window, snippet);
    if (minDistance >= distance) {
      minDistance = distance;
      index = i;
    }
  }

  if (minDistance === Number.MAX_SAFE_INTEGER && index === 0) {
    return null;
  }

  const startLine = index;
  const endLine = index + snippetLines.length - 1;

  return { range: { startLine, endLine }, score: minDistance };
}

export function getSmartApplyRange(documentText, snippet) {
  const applyRange = fuzzyApplyRange(documentText, snippet);
  if (!applyRange) {
    return undefined;
  }
  if (applyRange.range.startLine === applyRange.range.endLine || documentText.trim() === "") {
    return { range: applyRange.range, action: "insert", score: applyRange.score };
  }
  return { range: applyRange.range, action: "replace", score: applyRange.score };
}



