import assert from "node:assert/strict";
import test from "node:test";
import {
  normalizeAskPayload,
  parseChipixFences,
} from "../../src/components/thread-first/chipixFences.js";

const ASK = "ask";

test("parseChipixFences extracts ask card and strips fence from prose", () => {
  const text = `Two things before I start.

\`\`\`chipix:ask
{
  "question": "What data width?",
  "sub": "32 or 64 is typical",
  "choices": [
    { "id": "w32", "label": "32-bit" },
    { "id": "w64", "label": "64-bit" }
  ]
}
\`\`\`
`;
  const { items, prose } = parseChipixFences(text);
  assert.equal(items.length, 1);
  assert.equal(items[0].kind, ASK);
  assert.equal(items[0].payload.question, "What data width?");
  assert.equal(items[0].payload.choices.length, 2);
  assert.match(prose, /Two things/);
  assert.doesNotMatch(prose, /chipix:ask/);
});

test("parseChipixFences supports multiple ask blocks", () => {
  const text = `\`\`\`chipix:ask
{"question":"A?","choices":[{"id":"a","label":"Yes"}]}
\`\`\`

\`\`\`chipix:ask
{"question":"B?","choices":[{"id":"b","label":"No"}]}
\`\`\``;
  const { items } = parseChipixFences(text);
  assert.equal(items.length, 2);
  assert.equal(items[0].payload.question, "A?");
  assert.equal(items[1].payload.question, "B?");
});

test("normalizeAskPayload fills defaults when choices missing", () => {
  const p = normalizeAskPayload({ question: "Pick one" });
  assert.equal(p.question, "Pick one");
  assert.ok(p.choices.length >= 2);
});

test("parseChipixFences dedupes repeated prose paragraphs", () => {
  const dup = "Same intro.\n\nSame intro.";
  const { prose } = parseChipixFences(dup);
  assert.equal(prose, "Same intro.");
});

test("parseChipixFences extracts plan with Implement actions", () => {
  const text = `\`\`\`chipix:plan
{
  "phase": "design",
  "title": "RTL design plan",
  "summary": "32-bit sync FIFO with gray pointers.",
  "assumptions": ["Active-low async reset"],
  "steps": [{ "id": "fifo", "label": "sync_fifo.sv" }]
}
\`\`\``;
  const { items } = parseChipixFences(text);
  assert.equal(items.length, 1);
  assert.equal(items[0].kind, "plan");
  assert.equal(items[0].payload.phase, "design");
  assert.equal(items[0].payload.summary, "32-bit sync FIFO with gray pointers.");
  assert.equal(items[0].payload._source, "rtl-design");
  assert.equal(items[0].payload.actions[0].label, "Implement");
});
