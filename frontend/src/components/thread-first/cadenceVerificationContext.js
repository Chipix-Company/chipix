/** Build agent WS context for Cadence verification turns. */

export function isCadenceRunPrompt(text = "") {
  return /\b(run|start|execute|test|simulate)\b.*\b(cadence|xcelium|xrun)\b/i.test(text)
    || /\b(cadence|xcelium|xrun)\b.*\b(run|start|execute|simulate|test)\b/i.test(text);
}

export function buildCadenceVerificationContext({
  projectId = null,
  executionResult = null,
  verdictPayload = null,
  selectedModule = null,
  mentalModelRevisionId = null,
} = {}) {
  const artifactIds = verdictPayload?.generatedArtifactIds
    || collectArtifactIds(executionResult);
  const brief = verdictPayload?.executionBrief
    || (executionResult ? safeJson(executionResult) : "");
  const cadenceStatus = verdictPayload?.cadenceSimulationStatus
    || executionResult?.results?.uvm?.cadence_simulation?.status
    || "not_run";

  return {
    cadence_verification_mode: true,
    ...(projectId ? { project_id: projectId } : {}),
    generated_artifact_ids: artifactIds,
    uvm_top_module: verdictPayload?.uvmTopModule || "top_tb",
    cadence_simulation_status: cadenceStatus,
    staged_execution_brief: brief,
    ...(selectedModule ? { selected_module: selectedModule } : {}),
    ...(mentalModelRevisionId ? { mental_model_revision_id: mentalModelRevisionId } : {}),
  };
}

function collectArtifactIds(result) {
  if (!result) return [];
  const ids = [];
  const add = (value) => {
    if (!value) return;
    if (Array.isArray(value)) {
      value.forEach(add);
      return;
    }
    const id = String(value).trim();
    if (id && !ids.includes(id)) ids.push(id);
  };
  Object.values(result?.results || {}).forEach((section) => {
    if (section && typeof section === "object") add(section.published_artifact_ids);
  });
  add(result?.published_artifact_ids);
  return ids;
}

function safeJson(value) {
  try {
    const text = JSON.stringify(value);
    return text.length > 14000 ? text.slice(0, 14000) : text;
  } catch {
    return String(value || "").slice(0, 4000);
  }
}

export function cadenceSimulationPending(stagedStage, cadenceTone, executionResult, verdictPayload) {
  if (stagedStage !== "completed") return false;
  if (cadenceTone !== "ok") return false;
  const status = verdictPayload?.cadenceSimulationStatus
    || executionResult?.results?.uvm?.cadence_simulation?.status
    || "not_run";
  return status === "not_run";
}
