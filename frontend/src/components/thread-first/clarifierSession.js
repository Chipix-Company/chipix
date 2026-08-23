/**
 * Helpers for the step-by-step clarifier overlay (System B).
 */

/** Strip common markdown markers for fuzzy text comparison. */
export function stripMarkdownPlain(text) {
  return String(text || "")
    .replace(/\*\*/g, "")
    .replace(/__/g, "")
    .replace(/`/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .trim();
}

/**
 * Split a long markdown question into context body + short headline.
 * Agents often embed a design recap and end with "Which X would you like?"
 */
export function parseClarifierQuestion(question) {
  const text = String(question || "").trim();
  if (!text) return { body: "", headline: "" };

  const blocks = text.split(/\n\n+/).filter((b) => b.trim());
  if (blocks.length > 1) {
    const last = blocks[blocks.length - 1].trim();
    const plainLast = stripMarkdownPlain(last);
    const looksLikeQuestion =
      plainLast.endsWith("?") && plainLast.length <= 160 && !last.includes("\n- ");
    if (looksLikeQuestion) {
      return {
        body: blocks.slice(0, -1).join("\n\n").trim(),
        headline: last,
      };
    }
  }

  const lines = text.split("\n");
  if (lines.length > 1) {
    const lastLine = lines[lines.length - 1].trim();
    const plainLast = stripMarkdownPlain(lastLine);
    if (plainLast.endsWith("?") && plainLast.length <= 160) {
      return {
        body: lines.slice(0, -1).join("\n").trim(),
        headline: lastLine,
      };
    }
  }

  return { body: "", headline: text };
}

/** Remove a repeated headline from intro prose (agent often duplicates the ask). */
export function dedupeIntroHeadline(intro, headline) {
  const i = String(intro || "").trim();
  const h = String(headline || "").trim();
  if (!i || !h) return { intro: i, headline: h };

  const pi = stripMarkdownPlain(i);
  const ph = stripMarkdownPlain(h);
  if (!pi.endsWith(ph)) return { intro: i, headline: h };

  const lines = i.split("\n");
  while (lines.length > 0) {
    const tail = stripMarkdownPlain(lines[lines.length - 1]);
    if (!tail) {
      lines.pop();
      continue;
    }
    if (tail === ph || ph.endsWith(tail) || tail.endsWith(ph)) {
      lines.pop();
      continue;
    }
    break;
  }

  return { intro: lines.join("\n").trim(), headline: h };
}

/** Ensure every step has a "Something else" free-text option. */
export function ensureStepChoices(step) {
  const choices = Array.isArray(step?.choices) ? [...step.choices] : [];
  const hasOther = choices.some(
    (c) => c.allowsText || c.id === "other" || c.id === "custom",
  );
  if (!hasOther) {
    choices.push({
      id: "other",
      label: "Something else…",
      allowsText: true,
    });
  }
  return {
    ...step,
    choices: choices.map((c) => ({
      ...c,
      allowsText: Boolean(c.allowsText || c.id === "other" || c.id === "custom"),
    })),
  };
}

/** Fingerprint ask payloads so duplicate fences in one turn collapse to one step. */
export function clarifierStepKey(payload) {
  if (!payload || typeof payload !== "object") return "";
  const q = stripMarkdownPlain(payload.question || payload.q || "");
  const sub = stripMarkdownPlain(payload.sub || payload.hint || "");
  const choices = (payload.choices || [])
    .map((c) => {
      if (typeof c === "string") return c.trim();
      return String(c?.label || c?.text || c?.id || "").trim();
    })
    .filter(Boolean)
    .join("|");
  return `${q}\x00${sub}\x00${choices}`;
}

export function dedupeClarifierPayloads(rawSteps = []) {
  const seen = new Set();
  const out = [];
  for (const payload of rawSteps) {
    const key = clarifierStepKey(payload);
    if (key && seen.has(key)) continue;
    if (key) seen.add(key);
    out.push(payload);
  }
  return out;
}

export function prepareClarifierSteps(rawSteps = []) {
  return dedupeClarifierPayloads(rawSteps).map((payload, index) =>
    ensureStepChoices({
      stepId: payload.stepId || `clarify-${index}`,
      question: payload.question || "Quick question",
      sub: payload.sub,
      choices: payload.choices || [],
    }),
  );
}

/**
 * Build the single user message sent after the overlay completes.
 */
export function formatClarifierBundle(steps, answers) {
  const lines = steps
    .map((step) => {
      const ans = answers[step.stepId];
      if (!ans) return null;
      const value = (ans.customText || ans.label || "").trim();
      if (!value) return null;
      const q = String(step.question || "").trim();
      return q ? `${q}: ${value}` : value;
    })
    .filter(Boolean);

  if (!lines.length) return "";
  if (lines.length === 1) return lines[0];
  return `Here are my answers:\n\n${lines.map((l) => `• ${l}`).join("\n")}`;
}

/** Short recap for a thread Said card after the user completes the wizard. */
export function formatClarifierRecap(steps, answers) {
  const lines = steps
    .map((step) => {
      const ans = answers[step.stepId];
      if (!ans) return null;
      const value = (ans.customText || ans.label || "").trim();
      if (!value) return null;
      return `**${step.question}** — ${value}`;
    })
    .filter(Boolean);
  if (!lines.length) return null;
  return `**Your answers**\n\n${lines.join("\n\n")}`;
}
