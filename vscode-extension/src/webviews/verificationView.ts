import * as vscode from "vscode";
import { errorToMessage } from "../api/chipixClient";
import { SimulatorDiagnostics } from "../diagnostics/simulatorDiagnostics";
import { ChipixState } from "../state/chipixState";
import { webviewHtml } from "./webviewBase";

export class VerificationViewProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly state: ChipixState,
    private readonly diagnostics: SimulatorDiagnostics,
  ) {
    this.context.subscriptions.push(this.state.changed.event(() => this.postState()));
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, "webview-ui")],
    };
    view.webview.html = webviewHtml(this.context, view.webview, "verification.js");
    view.webview.onDidReceiveMessage((msg) => this.handleMessage(msg), undefined, this.context.subscriptions);
    this.postState();
  }

  private async handleMessage(msg: any): Promise<void> {
    try {
      if (msg.type === "ready") this.postState();
      if (msg.type === "prepare") await this.state.prepareVerification();
      if (msg.type === "plan") await this.state.generatePlan();
      if (msg.type === "execute") await this.state.executePlan();
      if (msg.type === "uploadSimulatorLog") {
        await this.state.uploadSimulatorLog();
        const latestPatchMeta = this.state.patches?.[0]?.metadata;
        const analysis = latestPatchMeta?.analysis || latestPatchMeta?.root_causes ? latestPatchMeta : undefined;
        if (analysis) await this.diagnostics.publishFromAnalysis(analysis);
      }
    } catch (err: any) {
      const message = errorToMessage(err, "Verification action failed");
      vscode.window.showErrorMessage(`Verification action failed: ${message}`);
      this.postState(message);
    }
  }

  postState(error = ""): void {
    this.view?.webview.postMessage({
      type: "state",
      state: {
        status: this.state.statusMessage,
        prepared: this.state.prepared,
        plan: this.state.plan,
        events: this.state.events,
        result: this.state.result,
        patches: this.state.patches,
        error,
      },
    });
  }
}
