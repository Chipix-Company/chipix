import { Agent } from "@mastra/core/agent";
import { BENCH_CONFIG, BENCH_PROMPTS, printSummary, summarizeMetrics } from "./prompts.mjs";

function ensureCredentials() {
  const provider = String(BENCH_CONFIG.provider || "").toLowerCase();

  if (provider === "google") {
    const googleKey = process.env.GOOGLE_GENERATIVE_AI_API_KEY || process.env.GOOGLE_API_KEY;
    if (!googleKey) {
      throw new Error("GOOGLE_GENERATIVE_AI_API_KEY or GOOGLE_API_KEY is required for Google benchmark.");
    }
    process.env.GOOGLE_GENERATIVE_AI_API_KEY = googleKey;
    return;
  }

  if (!process.env.OPENAI_API_KEY) {
    throw new Error("OPENAI_API_KEY is required for OpenAI benchmark.");
  }
}

async function runMastraBench() {
  ensureCredentials();

  const provider = String(BENCH_CONFIG.provider || "google").toLowerCase();

  const agent = new Agent({
    id: "bench-agent",
    name: "Bench Agent",
    instructions: "You are a concise engineering assistant. Keep answers compact and technical.",
    model: `${provider}/${BENCH_CONFIG.model}`,
  });

  const runs = [];
  const selectedPrompts = BENCH_PROMPTS.slice(0, BENCH_CONFIG.maxRuns);

  for (let i = 0; i < selectedPrompts.length; i += 1) {
    const prompt = selectedPrompts[i];
    const started = Date.now();

    try {
      const result = await agent.generate(prompt, {
        maxSteps: 1,
        modelSettings: {
          maxTokens: BENCH_CONFIG.maxOutputTokens,
          temperature: BENCH_CONFIG.temperature,
        },
      });

      const text = String(result?.text || result?.content || "");
      runs.push({
        run: i + 1,
        ok: true,
        latencyMs: Date.now() - started,
        outputChars: text.length,
        usage: result?.usage || null,
      });
    } catch (error) {
      runs.push({
        run: i + 1,
        ok: false,
        latencyMs: Date.now() - started,
        outputChars: 0,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  const summary = summarizeMetrics("Mastra Agent SDK", runs);
  printSummary(summary);
  return summary;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  runMastraBench().catch((error) => {
    console.error("Mastra benchmark failed:", error.message || error);
    process.exitCode = 1;
  });
}

export { runMastraBench };
