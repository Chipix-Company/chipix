import assert from "node:assert/strict";
import test from "node:test";
import { mergeAssistantTailText } from "../../src/components/thread-first/assistantBufferMerge.js";
import {
  dedupeClarifierPayloads,
  prepareClarifierSteps,
} from "../../src/components/thread-first/clarifierSession.js";
const ASK_BLOCK = `\`\`\`chipix:ask
{"question":"What width?","choices":[{"id":"a","label":"32-bit"}]}
\`\`\``;

function countAskFences(text) {
  return (String(text).match(/```chipix:ask/gi) || []).length;
}

test("mergeAssistantTailText replaces buffer when done payload repeats streamed text", () => {
  const streamed = `Intro.\n\n${ASK_BLOCK}`;
  const donePayload = streamed;
  assert.equal(mergeAssistantTailText(streamed, donePayload), streamed);
  assert.equal(mergeAssistantTailText(streamed, donePayload).length, streamed.length);
});

test("mergeAssistantTailText extends buffer when done adds only new tail", () => {
  assert.equal(mergeAssistantTailText("Hello", "Hello world"), "Hello world");
});

test("mergeAssistantTailText prevents doubled ask fences from done+stream", () => {
  const streamed = `Intro.\n\n${ASK_BLOCK}`;
  const appended = streamed + streamed;
  assert.equal(countAskFences(appended), 2);
  const merged = mergeAssistantTailText(streamed, streamed);
  assert.equal(countAskFences(merged), 1);
});

test("dedupeClarifierPayloads collapses identical ask payloads", () => {
  const payload = {
    question: "Pick one",
    sub: "hint",
    choices: [{ id: "a", label: "Yes" }],
  };
  const steps = prepareClarifierSteps([payload, payload, { ...payload }]);
  assert.equal(steps.length, 1);
});

test("dedupeClarifierPayloads keeps distinct questions", () => {
  const steps = dedupeClarifierPayloads([
    { question: "A?", choices: [{ id: "1", label: "One" }] },
    { question: "B?", choices: [{ id: "2", label: "Two" }] },
  ]);
  assert.equal(steps.length, 2);
});
