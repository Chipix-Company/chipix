import API_BASE_URL from "../config";

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function jsonRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload?.detail || payload?.message || `Request failed (${response.status})`);
  }
  return payload;
}

export function getToolchainStatus(authToken) {
  return jsonRequest("/api/v1/eda/toolchain/status", {
    headers: authHeaders(authToken),
  });
}

// Rich per-simulator detection (Cadence Xcelium availability, license, platform,
// UVM). Pass refresh=true to re-probe instead of using the cached result.
export function getSimulators(authToken, { refresh = false } = {}) {
  const suffix = refresh ? "?refresh=1" : "";
  return jsonRequest(`/api/v1/tools/simulators${suffix}`, {
    headers: authHeaders(authToken),
  });
}

// Read the persisted manual Cadence override (xrun path / setup script / license).
export function getXceliumConfig(authToken) {
  return jsonRequest("/api/v1/tools/simulators/xcelium/config", {
    headers: authHeaders(authToken),
  });
}

// Manually point ChipVerify at Cadence when auto-detection fails. Any field left
// undefined is unchanged; an empty string clears it. Returns { config, detection }.
export function configureXcelium(
  { xrunBin, setupScript, setupShell, license } = {},
  authToken,
) {
  const body = {};
  if (xrunBin !== undefined) body.xrun_bin = xrunBin;
  if (setupScript !== undefined) body.setup_script = setupScript;
  if (setupShell !== undefined) body.setup_shell = setupShell;
  if (license !== undefined) body.license = license;
  return jsonRequest("/api/v1/tools/simulators/xcelium/configure", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify(body),
  });
}

// Remove the manual Cadence override and revert to launch defaults.
export function clearXceliumConfig(authToken) {
  return jsonRequest("/api/v1/tools/simulators/xcelium/config", {
    method: "DELETE",
    headers: authHeaders(authToken),
  });
}

// Run the hardcoded FIFO UVM fixture through the production Cadence compile path.
// Optional override fields are applied before the smoke compile. Returns
// { status, message, detection, compile_gate, fixture_paths, duration_ms }.
export function testCadenceIntegration(
  { xrunBin, setupScript, setupShell, license, timeoutSeconds = 180 } = {},
  authToken,
) {
  const body = { timeout_seconds: timeoutSeconds };
  if (xrunBin !== undefined) body.xrun_bin = xrunBin;
  if (setupScript !== undefined) body.setup_script = setupScript;
  if (setupShell !== undefined) body.setup_shell = setupShell;
  if (license !== undefined) body.license = license;
  return jsonRequest("/api/v1/tools/simulators/xcelium/integration-test", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify(body),
  });
}

// Single-file SystemVerilog syntax check through Cadence `xrun -compile`.
export function checkXceliumSyntax({ filename, content, incdirs = [], uvm = false }, authToken) {
  return jsonRequest("/api/v1/tools/simulators/xcelium/syntax-check", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify({ filename, content, incdirs, uvm }),
  });
}

export function createSimulatorRun(projectId, body = {}, authToken) {
  return jsonRequest(`/api/v1/projects/${projectId}/simulate`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(authToken) },
    body: JSON.stringify({
      simulator: "xcelium",
      generated_artifact_ids: body.generatedArtifactIds || body.generated_artifact_ids || [],
      top_module: body.topModule || body.top_module || "top_tb",
      uvm_testname: body.uvmTestname || body.uvm_testname || "",
      timeout_seconds: body.timeoutSeconds || body.timeout_seconds || 300,
      auto_repair_generated: body.autoRepair !== false,
      max_repair_rounds: body.maxRepairRounds ?? body.max_repair_rounds ?? 3,
    }),
  });
}

export function getSimulatorRunStatus(projectId, runId, authToken) {
  return jsonRequest(`/api/v1/projects/${projectId}/simulate/${runId}/status`, {
    headers: authHeaders(authToken),
  });
}

export function getSimulatorRunLogs(projectId, runId, authToken) {
  return jsonRequest(`/api/v1/projects/${projectId}/simulate/${runId}/logs`, {
    headers: authHeaders(authToken),
  });
}

export function getSimulatorRunVerdict(projectId, runId, authToken) {
  return jsonRequest(`/api/v1/projects/${projectId}/simulate/${runId}/verdict`, {
    headers: authHeaders(authToken),
  });
}

export function renderNetlist({ projectId, filename, source, topModule }, authToken) {
  return jsonRequest("/api/v1/eda/netlist/render", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(authToken),
    },
    body: JSON.stringify({
      project_id: projectId,
      filename,
      source,
      top_module: topModule || undefined,
    }),
  });
}

export function createSimulation(
  { projectId, filename, rtlSource, topModule, testbenchSource, traceFormat = "vcd" },
  authToken,
) {
  return jsonRequest("/api/v1/eda/simulations", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(authToken),
    },
    body: JSON.stringify({
      project_id: projectId,
      filename,
      rtl_source: rtlSource,
      top_module: topModule,
      testbench_source: testbenchSource || undefined,
      trace_format: traceFormat,
    }),
  });
}

export function getSimulationStatus(simulationId, authToken) {
  return jsonRequest(`/api/v1/eda/simulations/${simulationId}`, {
    headers: authHeaders(authToken),
  });
}

export function getWaveformSlice(simulationId, { signals = [], startTime, endTime } = {}, authToken) {
  const params = new URLSearchParams();
  signals.forEach((signal) => params.append("signals", signal));
  if (startTime !== undefined && startTime !== null) params.set("start_time", String(startTime));
  if (endTime !== undefined && endTime !== null) params.set("end_time", String(endTime));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return jsonRequest(`/api/v1/eda/simulations/${simulationId}/waveform${suffix}`, {
    headers: authHeaders(authToken),
  });
}

export function streamSimulationEvents(simulationId, { afterSeq = 0, onEvent, onDone, onError }, authToken) {
  const controller = new AbortController();
  const params = new URLSearchParams();
  params.set("after_seq", String(afterSeq));

  (async () => {
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/eda/simulations/${simulationId}/events?${params.toString()}`,
        {
          signal: controller.signal,
          headers: authHeaders(authToken),
        },
      );
      if (!response.ok) {
        onError?.(new Error(`Event stream failed (${response.status})`));
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() || "";
        for (const chunk of chunks) {
          const dataLine = chunk
            .split("\n")
            .find((line) => line.startsWith("data: "));
          if (!dataLine) continue;
          const payload = JSON.parse(dataLine.slice(6));
          if (payload.type === "end") {
            onDone?.(payload);
            return;
          }
          onEvent?.(payload);
        }
      }
    } catch (error) {
      if (error?.name !== "AbortError") {
        onError?.(error);
      }
    }
  })();

  return () => controller.abort();
}



