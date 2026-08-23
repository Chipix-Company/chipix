/**
 * Parse `chipix:<kind>` fenced JSON blocks out of an assistant message.
 * See useRtlDesignStream.js for the full protocol description.
 */
import { THREAD_KINDS, makeItem } from "./types";
import { normalizePlanPayload } from "./implementationPlan";

const FENCE_RE = /```chipix:([a-z][a-z0-9-]*)\s*\n([\s\S]*?)\n```/gi;

const FENCE_KIND_TO_THREAD = {
  ask: "ask",
  "mental-model": "mentalModel",
  plan: "plan",
  verdict: "verdict",
  why: "why",
  waveform: "waveform",
  diff: "diff",
};

/** Normalize agent ask JSON → AskCard props (question, sub, choices). */
export function normalizeAskPayload(raw) {
  if (!raw || typeof raw !== "object") {
    return {
      question: "What should we do next?",
      choices: [
        { id: "recommend", label: "Recommend for me" },
        { id: "custom", label: "I'll specify in chat" },
      ],
    };
  }

  const question = String(
    raw.question || raw.q || raw.prompt || raw.title || "",
  ).trim();
  const sub = raw.sub || raw.hint || raw.subtitle || raw.description;
  let choices = raw.choices || raw.options || raw.answers || [];

  if (!Array.isArray(choices)) choices = [];
  choices = choices
    .map((c, i) => {
      if (typeof c === "string") {
        const label = c.trim();
        if (!label) return null;
        return { id: `c-${i}`, label };
      }
      if (!c || typeof c !== "object") return null;
      const label = String(c.label || c.text || c.name || c.value || "").trim();
      if (!label) return null;
      const id = String(c.id || c.value || `c-${i}`).trim() || `c-${i}`;
      const allowsText = Boolean(
        c.allowsText || c.allow_text || c.freeform || id === "other" || id === "custom",
      );
      return { id, label, allowsText };
    })
    .filter(Boolean);

  if (!choices.length) {
    choices = [
      { id: "recommend", label: "Recommend for me" },
      { id: "custom", label: "I'll specify in chat" },
    ];
  }

  return {
    question: question || "Quick question before I continue",
    sub: sub ? String(sub).trim() : undefined,
    choices,
    multi: Boolean(raw.multi || raw.allow_multiple || raw.allowMultiple),
    locked: false,
    chosenId: null,
  };
}

function injectDefaultActions(kind, payload) {
  if (kind === "plan") {
    return normalizePlanPayload(payload, { source: "rtl-design" });
  }
  return payload;
}

function dedupeProseParagraphs(prose) {
  if (!prose) return "";
  const parts = prose.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
  const seen = new Set();
  const unique = [];
  for (const p of parts) {
    const key = p.toLowerCase().replace(/\s+/g, " ");
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(p);
  }
  return unique.join("\n\n");
}

/** Drop immediately adjacent duplicate sentences inside each paragraph (model/stream repetition). */
export function dedupeRepeatedSentencesInParagraphs(prose) {
  if (!prose || typeof prose !== "string") return "";
  // Model tails often glue "…testbench.I haven't" with no space; sentence split misses that.
  const normalized = prose.replace(/(\.)(?=I\b)/g, "$1 ");
  return normalized
    .split(/\n{2,}/)
    .map((block) => {
      const trimmed = block.trim();
      if (!trimmed) return "";
      const sentences = trimmed.split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
      const out = [];
      let prevKey = null;
      for (const s of sentences) {
        const key = s.toLowerCase().replace(/\s+/g, " ");
        if (prevKey !== null && key === prevKey) continue;
        out.push(s);
        prevKey = key;
      }
      return out.join(" ");
    })
    .filter(Boolean)
    .join("\n\n");
}

function enrichFencePayload(kind, payload) {
  if (kind === "ask") {
    return normalizeAskPayload({ ...payload, _source: "rtl-design" });
  }
  if (kind === "plan") {
    return injectDefaultActions(kind, payload);
  }
  return { ...payload, _source: "rtl-design" };
}

export function parseChipixFences(text) {
  const items = [];
  if (!text || typeof text !== "string") return { items, prose: "" };

  let prose = text;
  FENCE_RE.lastIndex = 0;
  let match;
  while ((match = FENCE_RE.exec(text)) !== null) {
    const kind = FENCE_KIND_TO_THREAD[match[1].toLowerCase()];
    if (!kind) continue;
    let payload;
    try {
      payload = JSON.parse(match[2]);
    } catch {
      continue;
    }
    const enriched = enrichFencePayload(kind, payload);
    items.push({ kind, payload: enriched });
    prose = prose.replace(match[0], "");
  }
  const cleaned = dedupeProseParagraphs(prose.replace(/\n{3,}/g, "\n\n").trim());
  return { items, prose: dedupeRepeatedSentencesInParagraphs(cleaned) };
}

/** Strip fences and unclosed openers while streaming. */
export function streamingProse(text) {
  if (!text) return "";
  const { prose } = parseChipixFences(text);
  return prose.replace(/```chipix:[a-z][a-z0-9-]*[\s\S]*$/i, "").trim();
}

function hydrateFencePayload(threadKindSlug, enriched) {
  if (threadKindSlug === THREAD_KINDS.ASK) {
    return { ...enriched, locked: true };
  }
  if (threadKindSlug === THREAD_KINDS.PLAN) {
    return { ...enriched, superseded: false };
  }
  if (threadKindSlug === THREAD_KINDS.MENTAL_MODEL) {
    return { ...enriched, approved: true };
  }
  return enriched;
}

/**
 * Walk assistant/system message text in-order and emit thread items so reload
 * hydrates Ask / Plan / Mental model cards instead of showing raw ```chipix``` JSON.
 */
export function expandPersistedAssistantContent(text, options = {}) {
  const {
    ts = new Date().toISOString(),
    messageId = null,
    turnId,
    who = "assistant",
  } = options;

  const items = [];
  if (text == null) return items;

  const full = typeof text === "string" ? text : String(text);
  const fenceRe = /```chipix:([a-z][a-z0-9-]*)\s*\n([\s\S]*?)```/gi;

  let lastIndex = 0;
  let seq = 0;
  let match;

  const nextId = (suffix) =>
    (messageId ? `chat-${messageId}-${suffix}-${seq}` : undefined);

  const pushSaid = (chunk) => {
    const t = String(chunk || "").trim();
    if (!t) return;
    items.push(
      makeItem(
        THREAD_KINDS.SAID,
        { text: t, who },
        {
          id: nextId("said"),
          ts,
          turnId,
          backendMessageId: messageId || undefined,
        },
      ),
    );
    seq += 1;
  };

  while ((match = fenceRe.exec(full)) !== null) {
    pushSaid(full.slice(lastIndex, match.index));
    lastIndex = match.index + match[0].length;

    const langKey = match[1].toLowerCase();
    const threadKindSlug = FENCE_KIND_TO_THREAD[langKey];
    let payload;
    try {
      payload = JSON.parse(match[2]);
    } catch {
      pushSaid(match[0]);
      continue;
    }

    if (!threadKindSlug || !Object.values(THREAD_KINDS).includes(threadKindSlug)) {
      pushSaid(match[0]);
      continue;
    }

    const enriched = enrichFencePayload(threadKindSlug, payload);
    const payloadOut = hydrateFencePayload(threadKindSlug, enriched);

    items.push(
      makeItem(
        threadKindSlug,
        payloadOut,
        {
          id: nextId(threadKindSlug),
          ts,
          turnId,
          backendMessageId: messageId || undefined,
        },
      ),
    );
    seq += 1;
  }

  pushSaid(full.slice(lastIndex));

  if (!items.length && full.trim()) {
    items.push(
      makeItem(
        THREAD_KINDS.SAID,
        { text: full.trim(), who },
        {
          id: messageId ? `chat-${messageId}` : undefined,
          ts,
          turnId,
          backendMessageId: messageId || undefined,
        },
      ),
    );
  }

  return items;
}
