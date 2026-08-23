/**
 * Codebase graph API — status, report, interactive HTML, query.
 */

import API_BASE_URL from "../config";

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function fetchCodebaseGraphStatus(projectId, authToken) {
  const resp = await fetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/codebase-graph/status`,
    { headers: authHeaders(authToken) },
  );
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Graph status failed (${resp.status})`);
  }
  return resp.json();
}

export async function fetchCodebaseGraphReport(projectId, authToken) {
  const resp = await fetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/codebase-graph/report`,
    { headers: authHeaders(authToken) },
  );
  if (!resp.ok) {
    throw new Error(`Graph report unavailable (${resp.status})`);
  }
  return resp.text();
}

export function codebaseGraphHtmlUrl(projectId, authToken) {
  const q = authToken ? `?token=${encodeURIComponent(authToken)}` : "";
  return `${API_BASE_URL}/api/v1/projects/${projectId}/codebase-graph/html${q}`;
}

export async function rebuildCodebaseGraph(projectId, authToken) {
  const resp = await fetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/codebase-graph/rebuild`,
    { method: "POST", headers: authHeaders(authToken) },
  );
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Graph rebuild failed (${resp.status})`);
  }
  return resp.json();
}

export async function queryCodebaseGraph(projectId, question, authToken, { depth = 3, mode = "bfs" } = {}) {
  const resp = await fetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/codebase-graph/query`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
      body: JSON.stringify({ question, depth, mode }),
    },
  );
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Graph query failed (${resp.status})`);
  }
  return resp.json();
}
