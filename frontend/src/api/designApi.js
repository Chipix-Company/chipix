import API_BASE_URL from "../config";

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function generateDesign({ prompt, language, model, projectId, provider }, authToken) {
  const resp = await fetch(`${API_BASE_URL}/design/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify({ prompt, language, model, project_id: projectId, provider }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Design generation failed (${resp.status})`);
  }
  return resp.json();
}

export function streamDesignEvents(designId, onEvent, onError, onDone) {
  const url = `${API_BASE_URL}/design/${designId}/stream`;
  const controller = new AbortController();

  (async () => {
    try {
      const resp = await fetch(url, { signal: controller.signal });
      if (!resp.ok) {
        onError?.(new Error(`Stream failed: ${resp.status}`));
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.node === "__done__") {
                onDone?.(data.status);
                return;
              }
              onEvent?.(data);
            } catch {
              // skip malformed
            }
          }
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        onError?.(err);
      }
    }
  })();

  return () => controller.abort();
}

export function streamDesignGeneration(designId, callbacks) {
  const { onEvent, onReasoning, onContent, onStep, onProgress, onError, onDone } = callbacks;
  const url = `${API_BASE_URL}/design/${designId}/stream`;
  const controller = new AbortController();

  (async () => {
    try {
      const resp = await fetch(url, { signal: controller.signal });
      if (!resp.ok) {
        onError?.(new Error(`Stream failed: ${resp.status}`));
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.node === "__done__") {
                onDone?.(data.status);
                return;
              }
              if (onEvent) onEvent?.(data);
              
              if (data.reasoning && onReasoning) {
                onReasoning(data.reasoning);
              }
              
              if (data.content && onContent) {
                onContent(data.content);
              }
              
              if (data.step && onStep) {
                onStep(data.step, data.progress || 0);
              }
              
              if (typeof data.progress === "number" && onProgress) {
                onProgress(data.progress);
              }
            } catch {
              // skip malformed
            }
          }
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        onError?.(err);
      }
    }
  })();

  return () => controller.abort();
}

export async function getDesignStatus(designId, authToken) {
  const resp = await fetch(`${API_BASE_URL}/design/${designId}`, {
    headers: authHeaders(authToken),
  });
  if (!resp.ok) throw new Error(`Status check failed: ${resp.status}`);
  return resp.json();
}

export async function getVerifyPreview(designId, authToken) {
  const resp = await fetch(`${API_BASE_URL}/design/${designId}/verify-preview`, {
    headers: authHeaders(authToken),
  });
  if (!resp.ok) throw new Error(`Preview failed: ${resp.status}`);
  return resp.json();
}

export async function sendToVerification(designId, { projectId, rtlCode, strategy, description, enableCoverage, enableAssertions }, authToken) {
  const resp = await fetch(`${API_BASE_URL}/design/${designId}/send-to-verification`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify({
      project_id: projectId,
      rtl_code: rtlCode,
      strategy: strategy || "uvm",
      description,
      enable_coverage: enableCoverage !== false,
      enable_assertions: enableAssertions !== false,
    }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Send to verification failed (${resp.status})`);
  }
  return resp.json();
}

export async function verifyNow({ rtlCode, projectId, description, strategy, enableCoverage, enableAssertions, language }, authToken) {
  const resp = await fetch(`${API_BASE_URL}/design/verify-now`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify({
      rtl_code: rtlCode,
      project_id: projectId,
      description,
      strategy: strategy || "uvm",
      enable_coverage: enableCoverage !== false,
      enable_assertions: enableAssertions !== false,
      language: language || "systemverilog",
    }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Verify now failed (${resp.status})`);
  }
  return resp.json();
}

export async function startVerificationRun(runId, authToken) {
  const resp = await fetch(`${API_BASE_URL}/design/runs/${runId}/start`, {
    method: "POST",
    headers: authHeaders(authToken),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Start run failed (${resp.status})`);
  }
  return resp.json();
}

export async function getRunStatus(runId, authToken) {
  const resp = await fetch(`${API_BASE_URL}/design/runs/${runId}/status`, {
    headers: authHeaders(authToken),
  });
  if (!resp.ok) throw new Error(`Run status failed: ${resp.status}`);
  return resp.json();
}

export function streamRunLogs(runId, onEvent, onError, onDone) {
  const url = `${API_BASE_URL}/design/runs/${runId}/stream`;
  const controller = new AbortController();

  (async () => {
    try {
      const resp = await fetch(url, { signal: controller.signal });
      if (!resp.ok) {
        onError?.(new Error(`Log stream failed: ${resp.status}`));
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.__done__ || data.__timeout__) {
                onDone?.(data.status || "timeout");
                return;
              }
              onEvent?.(data);
            } catch {
              // skip
            }
          }
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        onError?.(err);
      }
    }
  })();

  return () => controller.abort();
}

export async function setupDefaultProject() {
  const resp = await fetch(`${API_BASE_URL}/design/setup/default-project`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Setup failed (${resp.status})`);
  }
  return resp.json();
}

export async function listProjects(authToken) {
  const resp = await fetch(`${API_BASE_URL}/design/setup/projects`, {
    headers: authHeaders(authToken),
  });
  if (!resp.ok) throw new Error(`List projects failed: ${resp.status}`);
  return resp.json();
}

export async function generateProjectFiles(designId, authToken) {
  const resp = await fetch(`${API_BASE_URL}/design/${designId}/files`, {
    method: "POST",
    headers: authHeaders(authToken),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `Generate files failed (${resp.status})`);
  }
  return resp.json();
}

export async function downloadProjectFile(designId, filename) {
  const resp = await fetch(`${API_BASE_URL}/design/${designId}/files/${filename}`);
  if (!resp.ok) throw new Error(`Download failed: ${resp.status}`);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}



