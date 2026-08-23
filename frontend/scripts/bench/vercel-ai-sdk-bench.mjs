import { generateText } from "ai";
import { google } from "@ai-sdk/google";
import { openai } from "@ai-sdk/openai";
import { BENCH_CONFIG, BENCH_PROMPTS, printSummary, summarizeMetrics } from "./prompts.mjs";

function resolveModel() {
  const provider = String(BENCH_CONFIG.provider || "").toLowerCase();

  if (provider === "google") {
    return google(BENCH_CONFIG.model);
  }

  return openai(BENCH_CONFIG.model);
}

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

async function runVercelBench() {
  ensureCredentials();

  const runs = [];
  const selectedPrompts = BENCH_PROMPTS.slice(0, BENCH_CONFIG.maxRuns);

  for (let i = 0; i < selectedPrompts.length; i += 1) {
    const prompt = selectedPrompts[i];
    const started = Date.now();

    try {
      const result = await generateText({
        model: resolveModel(),
        prompt,
        maxOutputTokens: BENCH_CONFIG.maxOutputTokens,
        temperature: BENCH_CONFIG.temperature,
      });

      runs.push({
        run: i + 1,
        ok: true,
        latencyMs: Date.now() - started,
        outputChars: String(result.text || "").length,
        usage: result.usage || null,
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

  const summary = summarizeMetrics("Vercel AI SDK", runs);
  printSummary(summary);
  return summary;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  runVercelBench().catch((error) => {
    console.error("Vercel benchmark failed:", error.message || error);
    process.exitCode = 1;
  });
}

export { runVercelBench };
