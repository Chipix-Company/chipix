import * as vscode from "vscode";
import { ChipixArtifact, ChipixPatch, errorToMessage } from "../api/chipixClient";
import { SimulatorDiagnostics } from "../diagnostics/simulatorDiagnostics";
import { getConfig, workspaceRoot } from "../config";
import { ChipixState } from "../state/chipixState";

export function registerCommands(
  context: vscode.ExtensionContext,
  state: ChipixState,
  diagnostics: SimulatorDiagnostics,
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("chipix.checkBackend", () => state.checkBackend(true)),
    vscode.commands.registerCommand("chipix.createOrLoadProject", async () => {
      const project = await state.ensureProject();
      vscode.window.showInformationMessage(`Chipix project ready: ${project.name}`);
      await state.refreshAll();
    }),
    vscode.commands.registerCommand("chipix.selectSpec", () => run("Attach spec", () => state.attachSpec())),
    vscode.commands.registerCommand("chipix.attachRtlWorkspace", () => run("Attach RTL workspace", () => state.attachRtlWorkspace())),
    vscode.commands.registerCommand("chipix.buildTruthCore", () => run("Build TruthCore", () => state.buildTruthCore())),
    vscode.commands.registerCommand("chipix.refreshTruthCore", () => run("Refresh TruthCore", () => state.refreshTruthCore(true))),
    vscode.commands.registerCommand("chipix.checkCurrentFile", () => checkCurrentFile(state, diagnostics)),
    vscode.commands.registerCommand("chipix.checkRtlWorkspace", () => checkRtlWorkspace(state, diagnostics)),
    vscode.commands.registerCommand("chipix.clearSyntaxDiagnostics", () => diagnostics.clearLintDiagnostics()),
    vscode.commands.registerCommand("chipix.prepareVerification", () => run("Prepare verification", () => state.prepareVerification())),
    vscode.commands.registerCommand("chipix.generatePlan", () => run("Generate plan", () => state.generatePlan())),
    vscode.commands.registerCommand("chipix.executePlan", () => run("Execute plan", () => state.executePlan())),
    vscode.commands.registerCommand("chipix.uploadSimulatorLog", async () => {
      await run("Upload simulator log", async () => {
        await state.uploadSimulatorLog();
        const latest = state.patches?.[0]?.metadata;
        if (latest?.analysis) await diagnostics.publishFromAnalysis(latest.analysis);
      });
    }),
    vscode.commands.registerCommand("chipix.refreshPatches", () => run("Refresh patches", () => state.refreshPatches())),
    vscode.commands.registerCommand("chipix.reviewPatch", (patch: ChipixPatch) => reviewPatch(context, state, patch)),
    vscode.commands.registerCommand("chipix.approvePatch", (patch: ChipixPatch) => approvePatch(state, patch)),
    vscode.commands.registerCommand("chipix.rejectPatch", (patch: ChipixPatch) => rejectPatch(state, patch)),
    vscode.commands.registerCommand("chipix.openGeneratedArtifact", (artifact: ChipixArtifact) => openGeneratedArtifact(state, artifact)),
    vscode.commands.registerCommand("chipix.openSettings", () => vscode.commands.executeCommand("workbench.action.openSettings", "chipix")),
  );
}

async function checkCurrentFile(state: ChipixState, diagnostics: SimulatorDiagnostics): Promise<void> {
  const document = vscode.window.activeTextEditor?.document;
  if (!document || !isSystemVerilogDocument(document)) {
    vscode.window.showWarningMessage("Open a Verilog or SystemVerilog file to run Chipix syntax checking.");
    return;
  }
  await run("Check current file", async () => {
    const project = await state.ensureProject();
    const filepath = vscode.workspace.asRelativePath(document.uri, false).replace(/\\/g, "/");
    const result = await state.client.lintSource(project.id, filepath, document.getText(), document.version);
    diagnostics.publishLintResult(document.uri, result);
    showLintSummary(result, 1);
  });
}

async function checkRtlWorkspace(state: ChipixState, diagnostics: SimulatorDiagnostics): Promise<void> {
  await run("Check RTL workspace", async () => {
    const project = await state.ensureProject();
    const files = await vscode.workspace.findFiles(
      "**/*.{v,sv,svh,vh}",
      "{**/node_modules/**,**/.git/**,**/chipix_out/**}",
    );
    if (!files.length) {
      vscode.window.showWarningMessage("No Verilog or SystemVerilog files were found in this workspace.");
      return;
    }
    let issueCount = 0;
    await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: "Chipix: checking RTL workspace",
        cancellable: true,
      },
      async (progress, token) => {
        for (let index = 0; index < files.length; index += 1) {
          if (token.isCancellationRequested) break;
          const uri = files[index];
          const document = await vscode.workspace.openTextDocument(uri);
          const filepath = vscode.workspace.asRelativePath(uri, false).replace(/\\/g, "/");
          const result = await state.client.lintSource(project.id, filepath, document.getText(), document.version);
          diagnostics.publishLintResult(uri, result);
          issueCount += Array.isArray(result?.diagnostics) ? result.diagnostics.length : 0;
          progress.report({
            increment: 100 / files.length,
            message: `${index + 1}/${files.length} ${filepath}`,
          });
        }
      },
    );
    vscode.window.showInformationMessage(
      `Chipix checked ${files.length} RTL file(s): ${issueCount} diagnostic(s).`,
    );
  });
}

function isSystemVerilogDocument(document: vscode.TextDocument): boolean {
  return /\.(?:sv|svh|v|vh)$/i.test(document.fileName);
}

function showLintSummary(result: any, fileCount: number): void {
  const diagnostics = Array.isArray(result?.diagnostics) ? result.diagnostics : [];
  const errors = diagnostics.filter(
    (diag: any) => String(diag?.severity || "").toLowerCase() === "error",
  ).length;
  const warnings = diagnostics.length - errors;
  vscode.window.showInformationMessage(
    `Chipix checked ${fileCount} file(s): ${errors} error(s), ${warnings} warning(s).`,
  );
}

async function run(label: string, fn: () => Promise<void>): Promise<void> {
  try {
    await fn();
  } catch (err: any) {
    vscode.window.showErrorMessage(`${label} failed: ${errorToMessage(err, label)}`);
  }
}

async function openGeneratedArtifact(state: ChipixState, artifact: ChipixArtifact): Promise<void> {
  if (!state.projectId) return;
  const root = workspaceRoot();
  if (!root) return;
  await state.syncGeneratedArtifacts();
  const rel = sanitizeRelativePath(artifact.metadata?.relative_path || artifact.filename);
  const uri = vscode.Uri.joinPath(root, getConfig().outputDir, ...rel.split("/"));
  await vscode.window.showTextDocument(uri, { preview: false });
}

async function reviewPatch(context: vscode.ExtensionContext, state: ChipixState, patch: ChipixPatch): Promise<void> {
  if (!state.projectId) return;
  const file = Array.isArray(patch.metadata?.files) ? patch.metadata.files[0] : null;
  if (!file?.proposed_content) {
    vscode.window.showInformationMessage(patch.diff_text || patch.reason || "Patch details are not available.");
    return;
  }
  const currentText = file.artifact_id
    ? (await state.client.getArtifact(state.projectId, file.artifact_id, true)).content || ""
    : "";
  const dir = vscode.Uri.joinPath(context.globalStorageUri, "patches", patch.id);
  await vscode.workspace.fs.createDirectory(dir);
  const currentUri = vscode.Uri.joinPath(dir, `current_${file.filename || "file.sv"}`);
  const proposedUri = vscode.Uri.joinPath(dir, `proposed_${file.filename || "file.sv"}`);
  await vscode.workspace.fs.writeFile(currentUri, Buffer.from(currentText, "utf-8"));
  await vscode.workspace.fs.writeFile(proposedUri, Buffer.from(file.proposed_content, "utf-8"));
  await vscode.commands.executeCommand("vscode.diff", currentUri, proposedUri, `Chipix Patch: ${patch.title}`);
  const choice = await vscode.window.showInformationMessage("Apply this Chipix patch?", "Apply Patch", "Reject", "Later");
  if (choice === "Apply Patch") await approvePatch(state, patch);
  if (choice === "Reject") await rejectPatch(state, patch);
}

async function approvePatch(state: ChipixState, patch: ChipixPatch): Promise<void> {
  if (!state.projectId) return;
  await state.client.approvePatch(state.projectId, patch.id);
  await state.refreshPatches();
  await state.refreshArtifacts();
  await state.syncGeneratedArtifacts();
  vscode.window.showInformationMessage("Chipix patch applied.");
}

async function rejectPatch(state: ChipixState, patch: ChipixPatch): Promise<void> {
  if (!state.projectId) return;
  const reason = await vscode.window.showInputBox({ prompt: "Reason for rejecting this patch", value: "Not needed" });
  await state.client.rejectPatch(state.projectId, patch.id, reason || "");
  await state.refreshPatches();
}

function sanitizeRelativePath(raw: string): string {
  return String(raw || "generated.txt")
    .replace(/\\/g, "/")
    .split("/")
    .filter((part) => part && part !== "." && part !== "..")
    .join("/") || "generated.txt";
}
