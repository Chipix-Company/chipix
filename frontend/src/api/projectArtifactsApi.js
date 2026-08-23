import API_BASE_URL from "../config";

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function parseError(resp) {
  const err = await resp.json().catch(() => ({}));
  const detail = err.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => d.msg || d.message || String(d)).join("; ");
  }
  return `Request failed (${resp.status})`;
}

/** Pin active spec/RTL revisions used by verification and mental-model builds. */
export async function setActiveProjectArtifacts(
  projectId,
  options = {},
  authToken,
) {
  const { specArtifactId, rtlArtifactId } = options;
  const body = {};
  if (Object.prototype.hasOwnProperty.call(options, "specArtifactId") && specArtifactId !== undefined) {
    body.spec_artifact_id = specArtifactId ?? null;
  }
  if (Object.prototype.hasOwnProperty.call(options, "rtlArtifactId") && rtlArtifactId !== undefined) {
    body.rtl_artifact_id = rtlArtifactId ?? null;
  }
  if (!Object.keys(body).length) return null;

  const resp = await fetch(`${API_BASE_URL}/api/v1/projects/${projectId}/artifacts/active`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    throw new Error(await parseError(resp));
  }
  return resp.json();
}

export async function renameProjectArtifact(projectId, artifactId, filename, authToken) {
  const resp = await fetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/artifacts/${artifactId}/rename`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
      body: JSON.stringify({ filename }),
    },
  );
  if (!resp.ok) {
    throw new Error(await parseError(resp));
  }
  return resp.json();
}

export async function deleteProjectArtifact(projectId, artifactId, authToken) {
  const resp = await fetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/artifacts/${artifactId}`,
    {
      method: "DELETE",
      headers: { ...authHeaders(authToken) },
    },
  );
  if (!resp.ok) {
    throw new Error(await parseError(resp));
  }
  return resp.json();
}

export async function updateProjectArtifactContent(projectId, artifactId, content, authToken) {
  const resp = await fetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/artifacts/${artifactId}/content`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
      body: JSON.stringify({ content: content || "" }),
    },
  );
  if (!resp.ok) {
    throw new Error(await parseError(resp));
  }
  return resp.json();
}
