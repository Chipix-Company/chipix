import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { CompletionDebouncer } from "../../src/components/thread-first/surfaces/editor/ideInlineCompletion/CompletionDebouncer.js";
import { CompletionMutex } from "../../src/components/thread-first/surfaces/editor/ideInlineCompletion/CompletionMutex.js";
import { SolutionCache } from "../../src/components/thread-first/surfaces/editor/ideInlineCompletion/SolutionCache.js";
import { cacheKeyForRequest, extractPrefixSuffix } from "../../src/components/thread-first/surfaces/editor/ideInlineCompletion/prefixSuffix.js";
import { formatCompletionDebugLine } from "../../src/components/thread-first/surfaces/editor/ideInlineCompletion/CompletionDebug.js";
import { isCompletableFile } from "../../src/components/thread-first/surfaces/editor/ideEditorUtils.js";

test("CompletionDebouncer manual trigger has zero delay", () => {
  const debouncer = new CompletionDebouncer();
  assert.equal(debouncer.computeDelayMs(1, "wire a", ""), 0);
});

test("CompletionDebouncer adaptive delay clamps 100-1000ms", () => {
  const debouncer = new CompletionDebouncer();
  debouncer.recordKeystroke();
  debouncer.recordKeystroke();
  const delay = debouncer.computeDelayMs(0, "wire a", "");
  assert.ok(delay >= 100);
  assert.ok(delay <= 1000);
});

test("CompletionDebouncer subtracts server latency", () => {
  const debouncer = new CompletionDebouncer();
  debouncer.noteLatency(800);
  const without = debouncer.computeDelayMs(0, "endmodule", "");
  debouncer.noteLatency(0);
  const baseline = debouncer.computeDelayMs(0, "endmodule", "");
  assert.ok(without <= baseline);
});

test("CompletionMutex combinedSignal aborts on Monaco cancel", () => {
  const mutex = new CompletionMutex();
  let cancelled = false;
  const monacoToken = {
    isCancellationRequested: false,
    onCancellationRequested(cb) {
      cancelled = true;
      cb();
    },
  };
  const signal = mutex.combinedSignal(monacoToken);
  assert.equal(signal.aborted, true);
  assert.equal(cancelled, true);
});

test("SolutionCache tryForward reuses prefix-stable suggestion", () => {
  const cache = new SolutionCache();
  cache.set("k1", { insertText: "clk;\n", completionId: "c1" }, {
    filepath: "top.sv",
    prefix: "wire ",
  });
  const forwarded = cache.tryForward({
    prefix: "wire c",
    suffix: "",
    filepath: "top.sv",
    language: "systemverilog",
    revisionKey: "1",
  });
  assert.equal(forwarded?.insertText, "lk;\n");
});

test("cacheKeyForRequest includes revisionKey", () => {
  const key = cacheKeyForRequest({
    prefix: "a",
    suffix: "b",
    filepath: "f.sv",
    language: "sv",
    revisionKey: "7",
  });
  assert.ok(key.includes("7"));
});

test("formatCompletionDebugLine shows phase and latency", () => {
  const line = formatCompletionDebugLine({
    phase: "ok",
    lastLatencyMs: 842,
    requestCount: 3,
  });
  assert.match(line, /ok/);
  assert.match(line, /842ms/);
  assert.match(line, /req 3/);
});

test("isCompletableFile accepts RTL extensions", () => {
  assert.equal(isCompletableFile("uart_tx.sv"), true);
  assert.equal(isCompletableFile("top.v"), true);
  assert.equal(isCompletableFile("readme.md"), false);
});

test("ideInlineCompletion index.js has no isCompleatableFile typo", () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const indexPath = path.join(
    here,
    "../../src/components/thread-first/surfaces/editor/ideInlineCompletion/index.js",
  );
  const source = fs.readFileSync(indexPath, "utf8");
  const typo = `is${"Compleatable"}File`;
  const correct = `is${"Completable"}File`;
  assert.equal(
    source.includes(typo),
    false,
    `found typo ${typo} — use ${correct} from ideEditorUtils`,
  );
  assert.ok(source.includes(correct), `expected ${correct} in index.js`);
});

test("index.js enables forward-stable candidates (Tabby pattern)", () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const indexPath = path.join(
    here,
    "../../src/components/thread-first/surfaces/editor/ideInlineCompletion/index.js",
  );
  const source = fs.readFileSync(indexPath, "utf8");
  assert.ok(
    source.includes("enableForwardStableCandidates: true"),
    "expected Tabby forward-stable flag",
  );
});

test("extractPrefixSuffix returns cursor metadata", () => {
  const model = {
    getLineCount: () => 3,
    getLineMaxColumn: () => 10,
    getValueInRange: (range) => {
      if (range.endColumn === 5) return "wire ";
      return "\nendmodule";
    },
  };
  const result = extractPrefixSuffix(model, { lineNumber: 2, column: 5 });
  assert.equal(result.prefix, "wire ");
  assert.equal(result.suffix, "\nendmodule");
});
