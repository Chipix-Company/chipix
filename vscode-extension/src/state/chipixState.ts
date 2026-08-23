import * as vscode from "vscode";
import { ChipixArtifact, ChipixClient, ChipixPatch, ChipixProject, ExecuteEvent } from "../api/chipixClient";
import { getConfig, workspaceRoot } from "../config";

const PROJECT_KEY = "chipix.projectId";
const LAST_PLAN_KEY = "chipix.lastPlan";
const LAST_MODEL_ID_KEY = "chipix.lastMentalModelRevisionId";

export class ChipixState {
  readonly changed = new vscode.EventEmitter<void>();
  readonly output = vscode.window.createOutputChannel("Chipix");
  readonly truthCoreOutput = vscode.window.createOutputChannel("Chipix: TruthCore");
  readonly verificationOutput = vscode.window.createOutputChannel("Chipix: Verification");
  readonly simulatorOutput = vscode.window.createOutputChannel("Chipix: Simulator");
  readonly repairOutput = vscode.window.createOutputChannel("Chipix: Repair");

  project?: ChipixProject;
  artifacts: Record<string, ChipixArtifact[]> = {};
  mentalModel: any = null;
  prepared: any = null;
  plan: any = null;
  result: any = null;
  events: ExecuteEvent[] = [];
  patches: ChipixPatch[] = [];
  backendStatus: "unknown" | "connected" | "disconnected" = "unknown";
  statusMessage = "Chipix is idle";

  constructor(private readonly context: vscode.ExtensionContext) {}

  get client(): ChipixClient {
    return ChipixClient.fromConfig(getConfig().backendUrl);
  }

  get projectId(): string | undefined {
    return this.project?.id || this.context.workspaceState.get<string>(PROJECT_KEY);
  }

  async checkBackend(show = true): Promise<void> {
    try {
      const status = await this.client.checkBackend();
      this.backendStatus = "connected";
      this.statusMessage = "Backend connected";
      this.output.appendLine(`[backend] connected: ${JSON.stringify(status?.tools || status || {}).slice(0, 500)}`);
      if (show) vscode.window.showInformationMessage("Chipix backend is reachable.");
    } catch (err: any) {
      this.backendStatus = "disconnected";
      this.statusMessage = `Backend disconnected: ${err?.message || err}`;
      if (show) vscode.window.showErrorMessage(`Chipix backend is not reachable: ${err?.message || err}`);
    } finally {
      this.changed.fire();
    }
  }

  async ensureProject(): Promise<ChipixProject> {
    if (this.projectId) {
      try {
        this.project = await this.client.getProject(this.projectId);
        this.changed.fire();
        return this.project;
      } catch {
        await this.context.workspaceState.update(PROJECT_KEY, undefined);
      }
    }

    const root = workspaceRoot();
    if (!root) throw new Error("Open a workspace folder before creating a Chipix project.");
    const name = vscode.workspace.name || root.fsPath.replace(/\\/g, "/").split("/").pop() || "Chipix Project";
    this.project = await this.client.createProject(name, `VS Code workspace: ${root.fsPath}`);
    await this.context.workspaceState.update(PROJECT_KEY, this.project.id);
    this.statusMessage = `Project ready: ${this.project.name}`;
    this.changed.fire();
    return this.project;
  }

  async refreshAll(): Promise<void> {
    await this.refreshArtifacts();
    await this.refreshTruthCore(false);
    await this.refreshPatches();
  }

  async refreshArtifacts(): Promise<void> {
    if (!this.projectId) return;
    const result = await this.client.listArtifacts(this.projectId);
    this.artifacts = result.artifacts || {};
    this.changed.fire();
  }

  async attachSpec(): Promise<void> {
    const picked = await vscode.window.showOpenDialog({
      canSelectFiles: true,
      canSelectFolders: false,
      canSelectMany: false,
      filters: { "Spec files": ["pdf", "md", "txt", "docx", "rst", "json"], "All files": ["*"] },
      title: "Select specification file",
    });
    if (!picked?.[0]) return;
    const project = await this.ensureProject();
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Uploading spec to Chipix" }, async () => {
      await this.client.uploadSpec(project.id, picked[0]);
    });
    this.output.appendLine(`[spec] attached ${picked[0].fsPath}`);
    await this.refreshArtifacts();
  }

  async attachRtlWorkspace(): Promise<void> {
    const root = workspaceRoot();
    if (!root) throw new Error("Open a workspace folder before attaching RTL.");
    const files = await vscode.workspace.findFiles("**/*.{v,sv,svh,vh,vhd,vhdl}", "{**/node_modules/**,**/.git/**,**/chipix_out/**}");
    if (!files.length) {
      vscode.window.showWarningMessage("No RTL files found in this workspace.");
      return;
    }
    const project = await this.ensureProject();
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: `Uploading ${files.length} RTL file(s) to Chipix` }, async () => {
      await this.client.uploadRtlProject(project.id, root, files);
    });
    this.output.appendLine(`[rtl] attached ${files.length} workspace RTL files`);
    await this.refreshArtifacts();
  }

  async buildTruthCore(): Promise<void> {
    const project = await this.ensureProject();
    await this.refreshArtifacts();
    const specCount = (this.artifacts.spec || []).length;
    const rtlCount = (this.artifacts.rtl || []).length;
    if (!rtlCount) {
      throw new Error("Attach RTL first. Use Chipix: Attach RTL Workspace, or click Attach RTL workspace in the TruthCore/Verification panel.");
    }
    if (!specCount) {
      const choice = await vscode.window.showWarningMessage(
        "No spec file is attached. TruthCore can try RTL-only analysis, but a spec gives better requirements and intent.",
        "Continue RTL-only",
        "Attach spec",
      );
      if (choice === "Attach spec") {
        await this.attachSpec();
        await this.refreshArtifacts();
      } else if (choice !== "Continue RTL-only") {
        return;
      }
    }
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Building TruthCore" }, async () => {
      const result = await this.client.buildMentalModel(project.id);
      const revisionId = result?.mental_model?.id || result?.id;
      if (revisionId) await this.context.workspaceState.update(LAST_MODEL_ID_KEY, revisionId);
      this.truthCoreOutput.appendLine(`[truthcore] build complete ${revisionId || ""}`);
    });
    await this.refreshTruthCore(true);
  }

  async refreshTruthCore(showErrors = true): Promise<void> {
    if (!this.projectId) return;
    try {
      this.mentalModel = await this.client.latestMentalModel(this.projectId);
      const revisionId = this.mentalModel?.id || this.mentalModel?.mental_model?.id;
      if (revisionId) await this.context.workspaceState.update(LAST_MODEL_ID_KEY, revisionId);
    } catch (err: any) {
      this.mentalModel = null;
      if (showErrors) vscode.window.showWarningMessage(`TruthCore not available yet: ${err?.message || err}`);
    } finally {
      this.changed.fire();
    }
  }

  async prepareVerification(): Promise<void> {
    const project = await this.ensureProject();
    this.prepared = await this.client.prepareVerification(project.id);
    this.verificationOutput.appendLine("[prepare] verification context prepared");
    this.changed.fire();
  }

  async generatePlan(): Promise<void> {
    const project = await this.ensureProject();
    const cfg = getConfig();
    const revisionId = this.prepared?.mental_model?.id || this.prepared?.mental_model_revision_id || this.context.workspaceState.get<string>(LAST_MODEL_ID_KEY);
    this.plan = await this.client.generatePlan(project.id, cfg.defaultVerificationType, revisionId);
    await this.context.workspaceState.update(LAST_PLAN_KEY, this.plan);
    this.verificationOutput.appendLine(`[plan] generated ${cfg.defaultVerificationType} plan`);
    this.changed.fire();
  }

  async executePlan(): Promise<void> {
    const project = await this.ensureProject();
    const cfg = getConfig();
    const plan = this.plan || this.context.workspaceState.get<any>(LAST_PLAN_KEY);
    if (!plan) throw new Error("Generate a verification plan before execution.");
    const approvedPlan = plan.plan || plan;
    const revisionId = plan.mental_model_revision_id || this.context.workspaceState.get<string>(LAST_MODEL_ID_KEY);
    this.events = [];
    this.result = null;
    this.changed.fire();
    this.result = await this.client.executeVerificationStream(
      project.id,
      cfg.defaultVerificationType,
      approvedPlan,
      revisionId,
      (event) => {
        this.events.push(event);
        this.verificationOutput.appendLine(`[${event.phase || event.type || "event"}] ${event.message || event.file || ""}`);
        this.changed.fire();
      },
    );
    this.verificationOutput.appendLine(`[result] ${this.result?.summary || this.result?.status || "complete"}`);
    await this.refreshArtifacts();
    await this.syncGeneratedArtifacts();
    this.changed.fire();
  }

  async syncGeneratedArtifacts(): Promise<void> {
    const root = workspaceRoot();
    if (!root || !this.projectId) return;
    await this.refreshArtifacts();
    const generated = this.artifacts.generated || [];
    const outDir = vscode.Uri.joinPath(root, getConfig().outputDir);
    for (const artifact of generated) {
      const rel = sanitizeRelativePath(artifact.metadata?.relative_path || artifact.filename);
      const target = vscode.Uri.joinPath(outDir, ...rel.split("/"));
      await vscode.workspace.fs.createDirectory(dirnameUri(target));
      const bytes = await this.client.downloadArtifact(this.projectId, artifact.id);
      await vscode.workspace.fs.writeFile(target, bytes);
    }
    if (generated.length) this.output.appendLine(`[artifacts] synced ${generated.length} generated files to ${getConfig().outputDir}`);
  }

  async uploadSimulatorLog(): Promise<void> {
    const project = await this.ensureProject();
    const picked = await vscode.window.showOpenDialog({
      canSelectFiles: true,
      canSelectFolders: false,
      canSelectMany: true,
      filters: { "Simulator logs": ["log", "txt", "out"], "All files": ["*"] },
      title: "Upload simulator log(s)",
    });
    if (!picked?.length) return;
    const generatedIds = (this.artifacts.generated || []).map((a) => a.id);
    const result = await this.client.uploadUvmLogs(project.id, picked, generatedIds, this.result?.run_id);
    this.repairOutput.appendLine(`[uvm-log] ${result?.analysis?.summary || "analysis complete"}`);
    await this.refreshPatches();
    this.changed.fire();
  }

  async refreshPatches(): Promise<void> {
    if (!this.projectId) return;
    this.patches = await this.client.listPatches(this.projectId);
    this.changed.fire();
  }
}

function sanitizeRelativePath(raw: string): string {
  return String(raw || "generated.txt")
    .replace(/\\/g, "/")
    .split("/")
    .filter((part) => part && part !== "." && part !== "..")
    .join("/") || "generated.txt";
}

function dirnameUri(uri: vscode.Uri): vscode.Uri {
  const fsPath = uri.fsPath.replace(/\\/g, "/");
  const parent = fsPath.slice(0, fsPath.lastIndexOf("/")) || fsPath;
  return vscode.Uri.file(parent);
}
