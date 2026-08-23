const { z } = require("zod");
const { createBackendBridgeClient } = require("./backendBridgeClient");

async function loadCreateTool() {
  const mod = await import("@mastra/core/tools");
  return mod.createTool || mod.default?.createTool || mod.default;
}

function coerceString(value) {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeArray(value) {
  return Array.isArray(value) ? value : [];
}

/**
 * Creates all backend bridge tools.
 * Tools call the Python FastAPI backend via HTTP and return structured results.
 *
 * Tools provided:
 *  - list_project_artifacts     : list files in a project
 *  - read_project_artifact      : read a single artifact by ID
 *  - count_project_artifacts    : count artifacts with optional filters
 *  - apply_code_to_artifact     : apply code edits (requires approval)
 *  - create_project_artifact    : create a new file artifact
 *  - run_simulation             : trigger a verification simulation run
 *  - delegate_to_rtl_backend    : forward heavy RTL/UVM tasks to the Python agent_ws.py
 */
async function createBackendBridgeTools(options = {}) {
  const client = createBackendBridgeClient(options);
  const createTool = await loadCreateTool();
  const backendBaseUrl =
    (options.backendBaseUrl || process.env.CHIPVERIFY_BACKEND_URL || "http://127.0.0.1:7348")
      .trim()
      .replace(/\/+$/, "");

  // ── list_project_artifacts ───────────────────────────────────────────────
  const listProjectArtifacts = createTool({
    id: "list_project_artifacts",
    description:
      "List all files (artifacts) in the current project. Always call this FIRST before reading " +
      "files to discover available artifact IDs. Returns id, filename, artifact_type for each file.",
    inputSchema: z.object({
      projectId: z.string().describe("The project UUID (use the one from workspace context)."),
    }),
    execute: async ({ projectId }) => {
      const pid = coerceString(projectId);
      if (!pid) {
        return { success: false, summary: "Missing projectId.", error: "projectId is required" };
      }

      const result = await client.requestJson("/api/v1/tools/listFiles", {
        method: "POST",
        body: { project_id: pid },
      });

      if (!result.success) {
        return { success: false, summary: "Failed to list project artifacts.", error: result.error };
      }

      const files = normalizeArray(result.data?.files);
      return {
        success: true,
        summary: `Found ${files.length} artifact(s).`,
        data: { files },
      };
    },
  });

  // ── read_project_artifact ────────────────────────────────────────────────
  const readProjectArtifact = createTool({
    id: "read_project_artifact",
    description:
      "Read the full content of a single artifact by its UUID. " +
      "Use list_project_artifacts first to get artifact IDs.",
    inputSchema: z.object({
      artifactId: z.string().describe("The artifact UUID (from list_project_artifacts)."),
    }),
    execute: async ({ artifactId }) => {
      const aid = coerceString(artifactId);
      if (!aid) {
        return { success: false, summary: "Missing artifactId.", error: "artifactId is required" };
      }

      const result = await client.requestJson("/api/v1/tools/readFile", {
        method: "POST",
        body: { artifact_id: aid },
      });

      if (!result.success) {
        return { success: false, summary: "Failed to read artifact.", error: result.error };
      }

      return {
        success: true,
        summary: `Loaded artifact ${aid}.`,
        data: {
          artifactId: aid,
          filename: result.data?.filename || null,
          artifactType: result.data?.artifact_type || null,
          language: result.data?.language || null,
          content: result.data?.content || "",
        },
      };
    },
  });

  // ── count_project_artifacts ──────────────────────────────────────────────
  const countProjectArtifacts = createTool({
    id: "count_project_artifacts",
    description:
      "Count project artifacts with optional filters. Supports filtering by artifact_type " +
      "(spec, rtl, generated), file extension, or filename substring.",
    inputSchema: z.object({
      projectId: z.string().describe("The project UUID."),
      artifactType: z
        .enum(["spec", "rtl", "generated"])
        .optional()
        .describe("Filter by artifact type."),
      extension: z.string().optional().describe("Filter by file extension, e.g. '.sv'."),
      nameContains: z.string().optional().describe("Filter filenames containing this string."),
    }),
    execute: async ({ projectId, artifactType, extension, nameContains }) => {
      const pid = coerceString(projectId);
      if (!pid) {
        return { success: false, summary: "Missing projectId.", error: "projectId is required" };
      }

      const result = await client.requestJson("/api/v1/tools/listFiles", {
        method: "POST",
        body: { project_id: pid },
      });

      if (!result.success) {
        return { success: false, summary: "Failed to count project artifacts.", error: result.error };
      }

      let files = normalizeArray(result.data?.files);

      if (artifactType) {
        files = files.filter((f) => f?.artifact_type === artifactType);
      }
      if (extension) {
        const ext = extension.startsWith(".") ? extension : `.${extension}`;
        files = files.filter((f) => coerceString(f?.filename).toLowerCase().endsWith(ext));
      }
      if (nameContains) {
        const needle = nameContains.toLowerCase();
        files = files.filter((f) => coerceString(f?.filename).toLowerCase().includes(needle));
      }

      return {
        success: true,
        summary: `Found ${files.length} artifact(s) matching filters.`,
        data: {
          count: files.length,
          projectId: pid,
          appliedFilters: { artifactType: artifactType || null, extension: extension || null, nameContains: nameContains || null },
          files,
        },
      };
    },
  });

  // ── apply_code_to_artifact ───────────────────────────────────────────────
  const applyCodeToArtifact = createTool({
    id: "apply_code_to_artifact",
    description:
      "Apply code edits to an existing backend artifact only. " +
      "Do not use this for brand-new files created in the local workspace; use create_project_artifact instead. " +
      "Strategies: replace_file (full overwrite), replace_selection (replace old_content), " +
      "smart_insert (append). Provide non-empty code and a real existing artifactId.",
    inputSchema: z.object({
      artifactId: z.string().describe("The artifact UUID to edit."),
      code: z.string().describe("The new code content or patch to apply."),
      strategy: z
        .enum(["replace_file", "replace_selection", "smart_insert"])
        .describe("Edit strategy to use."),
      oldContent: z
        .string()
        .optional()
        .describe("The exact existing text to replace (required for replace_selection)."),
    }),
    execute: async ({ artifactId, code, strategy, oldContent }) => {
      const aid = coerceString(artifactId);
      const nextCode = typeof code === "string" ? code : "";

      if (!aid) {
        return {
          success: false,
          summary: "Missing artifactId.",
          error: "artifactId is required for apply_code_to_artifact",
        };
      }

      if (!nextCode.trim()) {
        return {
          success: false,
          summary: "Refusing to apply empty code.",
          error: "Non-empty code is required. Use create_project_artifact for new files.",
        };
      }

      const body = {
        artifact_id: aid,
        code: nextCode,
        strategy: strategy || "replace_file",
        ...(oldContent ? { old_content: oldContent } : {}),
      };

      const result = await client.requestJson("/api/v1/tools/applyCodeToFile", {
        method: "POST",
        body,
      });

      if (!result.success) {
        return { success: false, summary: "Failed to apply code patch.", error: result.error };
      }

      return {
        success: true,
        summary: `Applied ${strategy} patch to artifact ${aid}.`,
        data: { artifactId: aid, strategy, diff: result.data?.diff || null },
      };
    },
  });

  // ── create_project_artifact ──────────────────────────────────────────────
  const createProjectArtifact = createTool({
    id: "create_project_artifact",
    description:
      "Create a new backend artifact for a file that does not already exist in the project. " +
      "Use this after workspace_write_file when you created a brand-new file locally. " +
      "artifact_type must be one of: spec, rtl, generated.",
    inputSchema: z.object({
      projectId: z.string().describe("The project UUID."),
      filename: z.string().describe("The filename to create, e.g. 'fifo_tb.sv'."),
      artifactType: z
        .enum(["spec", "rtl", "generated"])
        .describe("Type of artifact."),
      content: z.string().describe("The full file content to write."),
    }),
    execute: async ({ projectId, filename, artifactType, content }) => {
      const pid = coerceString(projectId);
      const fname = coerceString(filename);
      if (!pid || !fname) {
        return { success: false, summary: "Missing projectId or filename.", error: "projectId and filename are required" };
      }

      const result = await client.requestJson("/api/v1/tools/createFile", {
        method: "POST",
        body: {
          project_id: pid,
          filename: fname,
          artifact_type: artifactType || "generated",
          content: content || "",
        },
      });

      if (!result.success) {
        return { success: false, summary: "Failed to create artifact.", error: result.error };
      }

      const artifact = result.data?.artifact || result.data;
      return {
        success: true,
        summary: `Created artifact ${fname} (type: ${artifactType}).`,
        data: {
          artifactId: artifact?.id || null,
          filename: artifact?.filename || fname,
          artifactType: artifact?.artifact_type || artifactType,
          revision: artifact?.revision || 1,
        },
      };
    },
  });

  // ── run_simulation ───────────────────────────────────────────────────────
  const runSimulation = createTool({
    id: "run_simulation",
    description:
      "Trigger a verification simulation run for the project using the currently active " +
      "spec and RTL artifacts. Returns a run_id that can be used to monitor progress.",
    inputSchema: z.object({
      projectId: z.string().describe("The project UUID."),
    }),
    execute: async ({ projectId }) => {
      const pid = coerceString(projectId);
      if (!pid) {
        return { success: false, summary: "Missing projectId.", error: "projectId is required" };
      }

      const result = await client.requestJson("/api/v1/tools/runSimulation", {
        method: "POST",
        body: { project_id: pid },
      });

      if (!result.success) {
        return { success: false, summary: "Failed to start simulation run.", error: result.error };
      }

      const runId = result.data?.run_id || result.data?.runId || null;
      return {
        success: true,
        summary: `Simulation run started. Run ID: ${runId}.`,
        data: { runId },
      };
    },
  });

  // ── delegate_to_rtl_backend ──────────────────────────────────────────────
  // Forwards heavy RTL/UVM requests to the Python agent_ws.py orchestrator
  // via a simple HTTP streaming call. We accumulate all content and return it.
  const delegateToRtlBackend = createTool({
    id: "delegate_to_rtl_backend",
    description:
      "Delegate a complex RTL verification or generation task to the Python backend agent. " +
      "Use this for: UVM testbench generation, SystemVerilog assertion writing, " +
      "formal verification, RTL generation from spec, coverage analysis. " +
      "The backend orchestrator handles the full pipeline and streams back results.",
    inputSchema: z.object({
      request: z
        .string()
        .describe("The full user request to forward to the RTL backend agent."),
      projectId: z
        .string()
        .optional()
        .describe("Project UUID for backend context (use from workspace context if available)."),
    }),
    execute: async ({ request, projectId }) => {
      const pid = coerceString(projectId || "");

      try {
        // Use the simple HTTP ask endpoint — returns JSON with assistant_message
        const threadId = `rtl-delegate-${Date.now()}`;

        // First create a thread for the delegation
        let resolvedThreadId = threadId;
        if (pid) {
          try {
            const threadRes = await client.requestJson(
              `/api/v1/projects/${pid}/chat/threads`,
              { method: "POST", body: { title: `RTL Delegation: ${request.slice(0, 50)}` } },
            );
            if (threadRes.success && threadRes.data?.id) {
              resolvedThreadId = threadRes.data.id;
            }
          } catch {
            // Use ephemeral thread — backend will handle it
          }
        }

        // Stream the chat response
        const askResponse = await client.requestJson(
          `/api/v1/chat/threads/${resolvedThreadId}/ask`,
          { method: "POST", body: { prompt: request, context: pid ? { project_id: pid } : {} } },
        );

        if (!askResponse.success) {
          return {
            success: false,
            summary: "RTL backend delegation failed.",
            error: askResponse.error || "Backend unavailable",
          };
        }

        const assistantText =
          askResponse.data?.assistant_message?.content ||
          askResponse.data?.content ||
          "Backend returned no response.";

        return {
          success: true,
          summary: "RTL backend delegation completed.",
          data: { response: assistantText },
        };
      } catch (err) {
        return {
          success: false,
          summary: "RTL backend delegation error.",
          error: err?.message || String(err),
        };
      }
    },
  });

  return {
    listProjectArtifacts,
    readProjectArtifact,
    countProjectArtifacts,
    applyCodeToArtifact,
    createProjectArtifact,
    runSimulation,
    delegateToRtlBackend,
  };
}

module.exports = {
  createBackendBridgeTools,
};
