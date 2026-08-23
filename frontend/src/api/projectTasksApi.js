import API_BASE_URL from "../config";

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function extractApiErrorMessage(detail, status) {
  if (detail == null || detail === "") {
    return `Request failed (${status})`;
  }
  if (typeof detail === "string") {
    return detail;
  }
  if (typeof detail === "object") {
    if (typeof detail.message === "string" && detail.message.trim()) {
      return detail.message.trim();
    }
    if (typeof detail.msg === "string" && detail.msg.trim()) {
      return detail.msg.trim();
    }
    if (detail.code === "LICENSE_MISSING") {
      return "Product license is not configured. Restart ChipVerify or add license.key to the backend folder.";
    }
    if (typeof detail.code === "string" && detail.code.startsWith("LICENSE_")) {
      return detail.message || "Product license check failed.";
    }
  }
  try {
    return JSON.stringify(detail);
  } catch {
    return `Request failed (${status})`;
  }
}

function formatApiError(resp, body) {
  const message = extractApiErrorMessage(body?.detail ?? body?.message, resp.status);
  if (resp.status === 404 && (message === "Not Found" || message.includes("Not Found"))) {
    return new Error(
      "Task board API is unavailable. Restart ChipVerify (backend on port 7348) to load the latest server.",
    );
  }
  if (resp.status === 403 && message.includes("license")) {
    return new Error(message);
  }
  return new Error(message);
}

async function jsonFetch(url, options = {}) {
  const resp = await fetch(url, options);
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw formatApiError(resp, body);
  }
  return body;
}

export async function syncProjectTasksFromThreads(projectId, authToken) {
  return jsonFetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/tasks/sync-from-threads`,
    {
      method: "POST",
      headers: authHeaders(authToken),
    },
  );
}

export async function listProjectTasks(projectId, { status, assigneeThreadId, authToken } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (assigneeThreadId) params.set("assignee_thread_id", assigneeThreadId);
  const qs = params.toString();
  return jsonFetch(
    `${API_BASE_URL}/api/v1/projects/${projectId}/tasks${qs ? `?${qs}` : ""}`,
    { headers: authHeaders(authToken) },
  );
}

export async function createProjectTask(projectId, payload, authToken) {
  return jsonFetch(`${API_BASE_URL}/api/v1/projects/${projectId}/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify(payload),
  });
}

export async function getProjectTask(taskId, authToken) {
  return jsonFetch(`${API_BASE_URL}/api/v1/tasks/${taskId}`, {
    headers: authHeaders(authToken),
  });
}

export async function patchProjectTask(taskId, payload, authToken) {
  return jsonFetch(`${API_BASE_URL}/api/v1/tasks/${taskId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify(payload),
  });
}

export async function deleteProjectTask(taskId, authToken) {
  const resp = await fetch(`${API_BASE_URL}/api/v1/tasks/${taskId}`, {
    method: "DELETE",
    headers: authHeaders(authToken),
  });
  if (!resp.ok && resp.status !== 204) {
    const body = await resp.json().catch(() => ({}));
    throw formatApiError(resp, body);
  }
}

export async function startProjectTask(taskId, payload, authToken) {
  return jsonFetch(`${API_BASE_URL}/api/v1/tasks/${taskId}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify(payload || {}),
  });
}

export { extractApiErrorMessage, formatApiError };
