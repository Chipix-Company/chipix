import * as vscode from "vscode";
import { normalizeBackendUrl } from "../config";

export type ChipixProject = {
  id: string;
  name: string;
  slug?: string;
  description?: string;
};

export type ChipixArtifact = {
  id: string;
  artifact_type: "spec" | "rtl" | "generated" | string;
  filename: string;
  content_type?: string;
  metadata?: Record<string, any>;
  content?: string;
};

export type ChipixPatch = {
  id: string;
  title: string;
  reason?: string;
  status: string;
  diff_text?: string;
  target_artifact_id?: string;
  metadata?: Record<string, any>;
};

export type ExecuteEvent = {
  type?: string;
  phase?: string;
  message?: string;
  file?: string;
  result?: any;
  [key: string]: any;
};

export class ChipixClient {
  constructor(private readonly baseUrl: string) {}

  static fromConfig(baseUrl: string): ChipixClient {
    return new ChipixClient(normalizeBackendUrl(baseUrl));
  }

  async checkBackend(): Promise<any> {
    return this.requestJson("/api/v1/eda/toolchain/status");
  }

  async listProjects(): Promise<ChipixProject[]> {
    return this.requestJson("/api/v1/projects");
  }

  async createProject(name: string, description = ""): Promise<ChipixProject> {
    return this.requestJson("/api/v1/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description }),
    });
  }

  async getProject(projectId: string): Promise<ChipixProject> {
    return this.requestJson(`/api/v1/projects/${projectId}`);
  }

  async uploadSpec(projectId: string, file: vscode.Uri): Promise<any> {
    const form = new FormData();
    form.append("source", "vscode-extension");
    form.append("artifact_file", await blobFromUri(file), basename(file.fsPath));
    return this.requestJson(`/api/v1/projects/${projectId}/artifacts/spec`, {
      method: "POST",
      body: form,
    });
  }

  async uploadRtlProject(projectId: string, root: vscode.Uri, files: vscode.Uri[]): Promise<any> {
    const form = new FormData();
    form.append("source", "vscode-extension");
    form.append("archive_name", `${sanitizeName(vscode.workspace.name || "workspace")}_rtl.zip`);
    for (const file of files) {
      const rel = vscode.workspace.asRelativePath(file, false).replace(/\\/g, "/");
      form.append("relative_paths", rel);
      form.append("artifact_files", await blobFromUri(file), basename(file.fsPath));
    }
    return this.requestJson(`/api/v1/projects/${projectId}/artifacts/rtl-project`, {
      method: "POST",
      body: form,
    });
  }

  async listArtifacts(projectId: string): Promise<{ artifacts: Record<string, ChipixArtifact[]> }> {
    return this.requestJson(`/api/v1/projects/${projectId}/artifacts`);
  }

  async getArtifact(projectId: string, artifactId: string, includeContent = false): Promise<ChipixArtifact> {
    return this.requestJson(`/api/v1/projects/${projectId}/artifacts/${artifactId}?include_content=${includeContent ? "true" : "false"}`);
  }

  async downloadArtifact(projectId: string, artifactId: string): Promise<Uint8Array> {
    const resp = await fetch(`${this.baseUrl}/api/v1/projects/${projectId}/artifacts/${artifactId}/download`);
    if (!resp.ok) throw new Error(await responseError(resp, "Download artifact failed"));
    return new Uint8Array(await resp.arrayBuffer());
  }

  async buildMentalModel(projectId: string): Promise<any> {
    return this.requestJson(`/api/v1/projects/${projectId}/mental-models/build`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
  }

  async latestMentalModel(projectId: string): Promise<any> {
    return this.requestJson(`/api/v1/projects/${projectId}/mental-models/latest?include_content=true`);
  }

  async lintSource(
    projectId: string,
    filepath: string,
    content: string,
    version = 1,
  ): Promise<any> {
    return this.requestJson(`/api/v1/projects/${projectId}/lint`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepath, content, version }),
    });
  }

  async prepareVerification(projectId: string): Promise<any> {
    return this.requestJson(`/api/v1/projects/${projectId}/verification/prepare`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_module: null }),
    });
  }

  async generatePlan(projectId: string, verificationType: string, mentalModelRevisionId?: string): Promise<any> {
    return this.requestJson(`/api/v1/projects/${projectId}/verification/plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        verification_type: verificationType,
        mental_model_revision_id: mentalModelRevisionId || null,
        target_module: null,
      }),
    });
  }

  async refinePlan(projectId: string, verificationType: string, planJson: any, feedback: string, mentalModelRevisionId?: string): Promise<any> {
    return this.requestJson(`/api/v1/projects/${projectId}/verification/plan/refine`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        verification_type: verificationType,
        plan_json: planJson,
        feedback,
        mental_model_revision_id: mentalModelRevisionId || planJson?.mental_model_revision_id || null,
      }),
    });
  }

  async executeVerificationStream(
    projectId: string,
    verificationType: string,
    approvedPlan: any,
    mentalModelRevisionId: string | undefined,
    onEvent: (event: ExecuteEvent) => void,
  ): Promise<any> {
    const resp = await fetch(`${this.baseUrl}/api/v1/projects/${projectId}/verification/execute/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        verification_type: verificationType,
        mental_model_revision_id: mentalModelRevisionId || null,
        approved_plan: approvedPlan,
      }),
    });
    if (!resp.ok) throw new Error(await responseError(resp, "Execute verification failed"));
    if (!resp.body) return {};

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalResult: any = null;

    const handleLine = (line: string) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      try {
        const event = JSON.parse(trimmed);
        onEvent(event);
        if (event.type === "complete") finalResult = event.result;
        if (event.type === "error") throw new Error(event.message || "Verification failed");
      } catch (err: any) {
        onEvent({ type: "warning", phase: "execute", message: `Ignored malformed stream event: ${err?.message || err}` });
      }
    };

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) handleLine(line);
    }
    if (buffer.trim()) handleLine(buffer);
    return finalResult || { status: "stream_incomplete", summary: "Verification stream ended without final result." };
  }

  async uploadUvmLogs(projectId: string, files: vscode.Uri[], generatedArtifactIds: string[] = [], runId?: string): Promise<any> {
    const form = new FormData();
    for (const file of files) {
      form.append("files", await blobFromUri(file), basename(file.fsPath));
    }
    if (runId) form.append("run_id", runId);
    if (generatedArtifactIds.length) form.append("generated_artifact_ids", JSON.stringify(generatedArtifactIds));
    form.append("simulator", "auto");
    return this.requestJson(`/api/v1/projects/${projectId}/uvm-debug/logs`, { method: "POST", body: form });
  }

  async listPatches(projectId: string): Promise<ChipixPatch[]> {
    const result = await this.requestJson(`/api/v1/projects/${projectId}/patches`);
    return Array.isArray(result) ? result : result?.patches || [];
  }

  async approvePatch(projectId: string, patchId: string): Promise<any> {
    return this.requestJson(`/api/v1/projects/${projectId}/patches/${patchId}/approve`, { method: "POST" });
  }

  async rejectPatch(projectId: string, patchId: string, reason = ""): Promise<any> {
    return this.requestJson(`/api/v1/projects/${projectId}/patches/${patchId}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    });
  }

  private async requestJson(path: string, init: RequestInit = {}): Promise<any> {
    const resp = await fetch(`${this.baseUrl}${path}`, init);
    if (!resp.ok) throw new Error(await responseError(resp, `Request failed (${resp.status})`));
    return resp.json();
  }
}

async function blobFromUri(uri: vscode.Uri): Promise<Blob> {
  const bytes = await vscode.workspace.fs.readFile(uri);
  return new Blob([bytes as any]);
}

async function responseError(resp: Response, fallback: string): Promise<string> {
  try {
    const data = await resp.json();
    return errorToMessage(data?.detail || data?.message || data?.error || data, fallback);
  } catch {
    return `${fallback} (${resp.status})`;
  }
}

export function errorToMessage(value: any, fallback = "Unknown error"): string {
  if (!value) return fallback;
  if (typeof value === "string") return value;
  if (value instanceof Error) return value.message || fallback;
  if (Array.isArray(value)) {
    const lines = value
      .map((item) => {
        if (typeof item === "string") return item;
        const loc = Array.isArray(item?.loc) ? item.loc.join(".") : item?.loc;
        const msg = item?.msg || item?.message || item?.detail || safeJson(item);
        return loc ? `${loc}: ${msg}` : msg;
      })
      .filter(Boolean);
    return lines.length ? lines.join("; ") : fallback;
  }
  if (typeof value === "object") {
    const nested = value.detail || value.message || value.error || value.reason || value.summary;
    if (nested && nested !== value) return errorToMessage(nested, fallback);
    return safeJson(value);
  }
  return String(value || fallback);
}

function safeJson(value: any): string {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function basename(path: string): string {
  return path.replace(/\\/g, "/").split("/").pop() || "file";
}

function sanitizeName(name: string): string {
  return String(name || "workspace").replace(/[^a-z0-9_.-]+/gi, "_");
}
