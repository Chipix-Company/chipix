/**
 * Verification API — Client for mental model, verification, and patch endpoints.
 * 
 * Endpoints:
 *   Mental Model: build, query, get revision
 *   Verification:  verify block
 *   Patches:       list, view, approve, reject
 */

import API_BASE_URL from "../config";

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ═══════════════════════════════════════════════════════════════════════
// Mental Model API
// ═══════════════════════════════════════════════════════════════════════

export async function buildMentalModel(projectId, { targetModule, rootPath, specText } = {}, authToken) {
  const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/mental-models/build`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify({
      target_module: targetModule || null,
      root_path: rootPath || null,
      spec_text: specText || null,
    }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Build mental model failed (${resp.status})`);
  }
  return resp.json();
}

export async function queryMentalModel(projectId, query = "summary", authToken) {
  const resp = await fetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/mental-models/latest?query=${encodeURIComponent(query)}`,
    { headers: authHeaders(authToken) },
  );
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Query mental model failed (${resp.status})`);
  }
  return resp.json();
}

export async function getMentalModelRevision(projectId, revision, authToken) {
  const resp = await fetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/mental-models/${revision}`,
    { headers: authHeaders(authToken) },
  );
  if (!resp.ok) throw new Error(`Get revision failed (${resp.status})`);
  return resp.json();
}

// ═══════════════════════════════════════════════════════════════════════
// Verification API
// ═══════════════════════════════════════════════════════════════════════

export async function verifyBlock(projectId, { targetModule, verificationTypes } = {}, authToken) {
  const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify({
      target_module: targetModule || null,
      verification_types: verificationTypes || ["all"],
    }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Verification failed (${resp.status})`);
  }
  return resp.json();
}

/**
 * Step 1: Analyze design — build mental model + return verification options.
 */
export async function analyzeDesign(projectId, { targetModule } = {}, authToken) {
  const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify({ target_module: targetModule || null }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Design analysis failed (${resp.status})`);
  }
  return resp.json();
}

/**
 * Step 2: Run a specific verification type after user has chosen.
 * @param {"unitsim"|"formal"|"uvm"|"all"} verificationType
 */
export async function runVerification(projectId, verificationType, { targetModule } = {}, authToken) {
  const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/verify/${verificationType}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify({ target_module: targetModule || null }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Verification run failed (${resp.status})`);
  }
  return resp.json();
}


// ═══════════════════════════════════════════════════════════════════════
// Patches API
// ═══════════════════════════════════════════════════════════════════════

export async function listPatches(projectId, authToken) {
  const resp = await fetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/patches`,
    { headers: authHeaders(authToken) },
  );
  if (!resp.ok) throw new Error(`List patches failed (${resp.status})`);
  return resp.json();
}

export async function getPatch(projectId, patchId, authToken) {
  const resp = await fetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/patches/${patchId}`,
    { headers: authHeaders(authToken) },
  );
  if (!resp.ok) throw new Error(`Get patch failed (${resp.status})`);
  return resp.json();
}

export async function approvePatch(projectId, patchId, authToken) {
  const resp = await fetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/patches/${patchId}/approve`,
    {
      method: "POST",
      headers: authHeaders(authToken),
    },
  );
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Approve patch failed (${resp.status})`);
  }
  return resp.json();
}

export async function rejectPatch(projectId, patchId, reason = "", authToken) {
  const resp = await fetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/patches/${patchId}/reject`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
      body: JSON.stringify({ reason }),
    },
  );
  if (!resp.ok) throw new Error(`Reject patch failed (${resp.status})`);
  return resp.json();
}

export async function uploadUvmDebugLogs(
  projectId,
  files,
  { runId = null, generatedArtifactIds = [], simulator = "auto" } = {},
  authToken,
) {
  const form = new FormData();
  (Array.isArray(files) ? files : Array.from(files || [])).forEach((file) => {
    form.append("files", file);
  });
  if (runId) form.append("run_id", runId);
  if (generatedArtifactIds?.length) {
    form.append("generated_artifact_ids", JSON.stringify(generatedArtifactIds));
  }
  form.append("simulator", simulator || "auto");

  const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/uvm-debug/logs`, {
    method: "POST",
    headers: { ...authHeaders(authToken) },
    body: form,
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `UVM log analysis failed (${resp.status})`);
  }
  return resp.json();
}
