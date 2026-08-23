import { runVercelBench } from "./vercel-ai-sdk-bench.mjs";
import { runMastraBench } from "./mastra-agent-sdk-bench.mjs";

function recommend(vercel, mastra) {
  const vercelScore = (vercel.successRuns * 1000) - vercel.avgLatencyMs - (vercel.failedRuns * 500);
  const mastraScore = (mastra.successRuns * 1000) - mastra.avgLatencyMs - (mastra.failedRuns * 500);

  if (vercelScore === mastraScore) {
    return {
      winner: "tie",
      reason: "Both SDKs are effectively equivalent under this benchmark.",
    };
  }

  if (vercelScore > mastraScore) {
    return {
      winner: "Vercel AI SDK",
      reason: "Higher success-adjusted performance score in this run.",
    };
  }

  return {
    winner: "Mastra Agent SDK",
    reason: "Higher success-adjusted performance score in this run.",
  };
}

async function main() {
  console.log("Running benchmark for Vercel AI SDK...");
  const vercel = await runVercelBench();

  console.log("\nRunning benchmark for Mastra Agent SDK...");
  const mastra = await runMastraBench();

  const decision = recommend(vercel, mastra);
  console.log("\n=== Recommendation ===");
  console.log(`Winner: ${decision.winner}`);
  console.log(`Reason: ${decision.reason}`);
}

main().catch((error) => {
  console.error("Comparison failed:", error.message || error);
  process.exitCode = 1;
});
