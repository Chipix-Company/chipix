/**
 * Staged Verification API — ChipStack prepare → plan → refine → execute flow.
 */

import API_BASE_URL from "../config";

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Stage 1: Prepare — build/refresh mental model + get strategy recommendation.
 */
export async function prepareVerification(projectId, { targetModule } = {}, authToken) {
  const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/verification/prepare`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify({ target_module: targetModule || null }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Prepare verification failed (${resp.status})`);
  }
  return resp.json();
}

/**
 * Stage 2: Generate plan for chosen verification type.
 */
export async function generatePlan(projectId, { verificationType, mentalModelRevisionId, targetModule } = {}, authToken) {
  const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/verification/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify({
      verification_type: verificationType,
      mental_model_revision_id: mentalModelRevisionId,
      target_module: targetModule || null,
    }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Generate plan failed (${resp.status})`);
  }
  return resp.json();
}

/**
 * Stage 3: Refine plan with natural language feedback.
 */
export async function refinePlan(projectId, { verificationType, planJson, feedback, mentalModelRevisionId } = {}, authToken) {
  const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/verification/plan/refine`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify({
      verification_type: verificationType,
      plan_json: planJson,
      feedback,
      mental_model_revision_id: mentalModelRevisionId || planJson?.mental_model_revision_id || null,
    }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Refine plan failed (${resp.status})`);
  }
  return resp.json();
}

/**
 * Stage 4: Execute verification after user approval.
 */
export async function executeVerification(projectId, { verificationType, mentalModelRevisionId, approvedPlan } = {}, authToken) {
  const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/verification/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify({
      verification_type: verificationType,
      mental_model_revision_id: mentalModelRevisionId,
      approved_plan: approvedPlan,
    }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Execute verification failed (${resp.status})`);
  }
  return resp.json();
}

/**
 * Stage 4: Execute verification with newline-delimited streaming progress.
 * onEvent receives {type, phase, message, file, result, ...}.
 */
export async function executeVerificationStream(
  projectId,
  { verificationType, mentalModelRevisionId, approvedPlan } = {},
  authToken,
  onEvent,
) {
  const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/verification/execute/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify({
      verification_type: verificationType,
      mental_model_revision_id: mentalModelRevisionId,
      approved_plan: approvedPlan,
    }),
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Execute verification failed (${resp.status})`);
  }

  if (!resp.body) {
    return executeVerification(
      projectId,
      { verificationType, mentalModelRevisionId, approvedPlan },
      authToken,
    );
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult = null;
  const parseErrors = [];
  const receivedEvents = [];

  const handleEvent = (event) => {
    receivedEvents.push(event);
    if (onEvent) onEvent(event);
    if (event.type === "complete") finalResult = event.result;
    if (event.type === "error") throw new Error(event.message || "Verification execution failed");
  };

  const handleLine = (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    let event;
    try {
      event = JSON.parse(trimmed);
    } catch (err) {
      parseErrors.push({
        line: trimmed.slice(0, 500),
        error: err?.message || "Invalid JSON event",
      });
      if (onEvent) {
        onEvent({
          type: "warning",
          phase: "execute",
          message: `Ignored malformed verification stream event: ${err?.message || "invalid JSON"}`,
        });
      }
      return;
    }
    handleEvent(event);
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      handleLine(line);
    }
  }

  if (buffer.trim()) {
    handleLine(buffer);
  }

  if (!finalResult) {
    const result = {
      status: "stream_incomplete",
      summary: "Verification stream ended before the backend sent a final result. Generated files may still be available in the project artifacts.",
      results: {},
      stream_events: receivedEvents.slice(-50),
      stream_parse_errors: parseErrors,
    };
    if (onEvent) {
      onEvent({
        type: "warning",
        phase: "execute",
        message: result.summary,
        result,
      });
    }
    return result;
  }
  return finalResult;
}
