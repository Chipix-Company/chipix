import * as vscode from "vscode";
import { ChipixArtifact, ChipixPatch } from "../api/chipixClient";
import { getConfig } from "../config";
import { ChipixState } from "../state/chipixState";

type NodeKind = "action" | "status" | "artifact" | "patch" | "report" | "setting";

export class ChipixTreeItem extends vscode.TreeItem {
  constructor(
    label: string,
    collapsibleState: vscode.TreeItemCollapsibleState,
    readonly kind: NodeKind,
    command?: vscode.Command,
    description?: string,
  ) {
    super(label, collapsibleState);
    this.command = command;
    this.description = description;
    this.contextValue = kind;
  }
}

class BaseProvider implements vscode.TreeDataProvider<ChipixTreeItem> {
  protected readonly emitter = new vscode.EventEmitter<ChipixTreeItem | undefined | null | void>();
  readonly onDidChangeTreeData = this.emitter.event;

  constructor(protected readonly state: ChipixState) {
    this.state.changed.event(() => this.refresh());
  }

  refresh(): void {
    this.emitter.fire();
  }

  getTreeItem(element: ChipixTreeItem): vscode.TreeItem {
    return element;
  }

  getChildren(): vscode.ProviderResult<ChipixTreeItem[]> {
    return [];
  }

  protected action(label: string, command: string, description?: string): ChipixTreeItem {
    const item = new ChipixTreeItem(label, vscode.TreeItemCollapsibleState.None, "action", { command, title: label }, description);
    item.iconPath = new vscode.ThemeIcon("play");
    return item;
  }

  protected status(label: string, description?: string, icon = "circle-outline"): ChipixTreeItem {
    const item = new ChipixTreeItem(label, vscode.TreeItemCollapsibleState.None, "status", undefined, description);
    item.iconPath = new vscode.ThemeIcon(icon);
    return item;
  }
}

export class RunsProvider extends BaseProvider {
  getChildren(): ChipixTreeItem[] {
    const items: ChipixTreeItem[] = [
      this.status(this.state.backendStatus === "connected" ? "Backend connected" : "Backend status unknown", getConfig().backendUrl, this.state.backendStatus === "connected" ? "pass" : "warning"),
      this.action("Check backend", "chipix.checkBackend"),
      this.action("Upload simulator log", "chipix.uploadSimulatorLog"),
    ];
    for (const event of this.state.events.slice(-12).reverse()) {
      items.push(this.status(event.message || event.file || event.type || "event", event.phase || event.type, "pulse"));
    }
    return items;
  }
}

export class PatchesProvider extends BaseProvider {
  getChildren(): ChipixTreeItem[] {
    const patches = this.state.patches || [];
    if (!patches.length) {
      return [
        this.action("Refresh patches", "chipix.refreshPatches"),
        this.status("No pending patches", "Simulator repair proposals appear here", "check"),
      ];
    }
    return patches.map((patch) => {
      const item = new ChipixTreeItem(
        patch.title || `Patch ${patch.id.slice(0, 8)}`,
        vscode.TreeItemCollapsibleState.None,
        "patch",
        { command: "chipix.reviewPatch", title: "Review Patch", arguments: [patch] },
        patch.status,
      );
      item.iconPath = new vscode.ThemeIcon(patch.status === "awaiting_approval" ? "diff" : "check");
      return item;
    });
  }
}

export class ReportsProvider extends BaseProvider {
  getChildren(): ChipixTreeItem[] {
    const generated = this.state.artifacts.generated || [];
    const reports = generated.filter((artifact) => /\.(md|txt|json|html|pdf)$/i.test(artifact.filename || ""));
    if (!reports.length) return [this.status("No reports yet", "Closure reports will appear after runs", "book")];
    return reports.map((artifact) => artifactItem(artifact, "report"));
  }
}

export class SettingsProvider extends BaseProvider {
  getChildren(): ChipixTreeItem[] {
    const cfg = getConfig();
    return [
      this.action("Open extension settings", "chipix.openSettings"),
      this.status("Backend URL", cfg.backendUrl, "server"),
      this.status("Output directory", cfg.outputDir, "folder"),
      this.status("Default strategy", cfg.defaultVerificationType, "beaker"),
      this.status("Model", `${cfg.llmProvider} / ${cfg.model}`, "symbol-method"),
    ];
  }
}

export class GeneratedArtifactsProvider extends BaseProvider {
  getChildren(): ChipixTreeItem[] {
    const artifacts = this.state.artifacts.generated || [];
    if (!artifacts.length) return [this.status("No generated artifacts yet", "Execute a plan to populate chipix_out", "files")];
    return artifacts.slice(0, 80).map((artifact) => artifactItem(artifact, "artifact"));
  }
}

export class VerificationActionsProvider extends BaseProvider {
  getChildren(): ChipixTreeItem[] {
    const hasPlan = Boolean(this.state.plan);
    return [
      this.action("Create/load workspace project", "chipix.createOrLoadProject"),
      this.action("Attach spec file", "chipix.selectSpec"),
      this.action("Attach RTL workspace", "chipix.attachRtlWorkspace"),
      this.action("Prepare verification", "chipix.prepareVerification"),
      this.action("Generate plan", "chipix.generatePlan"),
      hasPlan ? this.action("Execute approved plan", "chipix.executePlan") : this.status("Generate a plan before execute", undefined, "lock"),
    ];
  }
}

function artifactItem(artifact: ChipixArtifact, kind: NodeKind): ChipixTreeItem {
  const item = new ChipixTreeItem(
    artifact.metadata?.relative_path || artifact.filename,
    vscode.TreeItemCollapsibleState.None,
    kind,
    { command: "chipix.openGeneratedArtifact", title: "Open Generated Artifact", arguments: [artifact] },
    artifact.artifact_type,
  );
  item.iconPath = new vscode.ThemeIcon("file-code");
  return item;
}

export function registerTreeViews(context: vscode.ExtensionContext, state: ChipixState): void {
  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("chipix.runs", new RunsProvider(state)),
    vscode.window.registerTreeDataProvider("chipix.patches", new PatchesProvider(state)),
    vscode.window.registerTreeDataProvider("chipix.reports", new ReportsProvider(state)),
    vscode.window.registerTreeDataProvider("chipix.settings", new SettingsProvider(state)),
  );
}

export { ChipixPatch };
