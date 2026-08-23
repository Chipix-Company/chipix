const test = require("node:test");
const assert = require("node:assert/strict");

const { _test } = require("./copilot");

const ENV_KEYS = [
  "CHIPVERIFY_LLM_PROVIDER",
  "MODEL_PROVIDER",
  "CHIPVERIFY_MASTRA_MODEL",
  "CHIPVERIFY_MODEL",
  "BEDROCK_MODEL",
  "MODEL_NAME",
];

function setTestEnv(t, values) {
  const previous = Object.fromEntries(ENV_KEYS.map((key) => [key, process.env[key]]));
  for (const key of ENV_KEYS) delete process.env[key];
  Object.assign(process.env, values);
  t.after(() => {
    for (const key of ENV_KEYS) {
      if (previous[key] === undefined) delete process.env[key];
      else process.env[key] = previous[key];
    }
  });
}

test("Bedrock copilot uses the backend route", { concurrency: false }, (t) => {
  setTestEnv(t, {
    CHIPVERIFY_LLM_PROVIDER: "bedrock",
    BEDROCK_MODEL: "deepseek.v3.2",
  });

  assert.equal(_test.shouldRouteCopilotThroughBackend(), true);
  assert.deepEqual(_test.resolveRuntimeModelMetadata(), {
    provider: "bedrock",
    model: "deepseek.v3.2",
  });
});

test("an explicit Mastra model keeps direct Mastra routing", { concurrency: false }, (t) => {
  setTestEnv(t, {
    CHIPVERIFY_LLM_PROVIDER: "bedrock",
    CHIPVERIFY_MASTRA_MODEL: "openai/custom-model",
  });

  assert.equal(_test.shouldRouteCopilotThroughBackend(), false);
});

test("a Mastra fetch failure can fall back to the backend", () => {
  assert.equal(_test.isMastraRecoverableFailure(new TypeError("fetch failed")), true);
});
