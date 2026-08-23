import * as vscode from "vscode";

export type ChipixConfig = {
  backendUrl: string;
  outputDir: string;
  defaultVerificationType: "uvm" | "formal" | "unitsim" | "all";
  autoStartBackend: boolean;
  llmProvider: string;
  model: string;
};

export function getConfig(): ChipixConfig {
  const cfg = vscode.workspace.getConfiguration("chipix");
  return {
    backendUrl: normalizeBackendUrl(cfg.get("backendUrl", "http://127.0.0.1:7348")),
    outputDir: cfg.get("outputDir", "chipix_out"),
    defaultVerificationType: cfg.get("defaultVerificationType", "uvm"),
    autoStartBackend: cfg.get("autoStartBackend", false),
    llmProvider: cfg.get("llmProvider", "gemini"),
    model: cfg.get("model", "gemini-2.5-pro"),
  };
}

export function normalizeBackendUrl(raw: string): string {
  const trimmed = String(raw || "").trim().replace(/\/+$/, "");
  return trimmed.replace(/\/api\/v1$/i, "") || "http://127.0.0.1:7348";
}

export function workspaceRoot(): vscode.Uri | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri;
}
