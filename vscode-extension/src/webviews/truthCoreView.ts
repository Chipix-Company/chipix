import * as vscode from "vscode";
import { errorToMessage } from "../api/chipixClient";
import { ChipixState } from "../state/chipixState";
import { webviewHtml } from "./webviewBase";

export class TruthCoreViewProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;

  constructor(private readonly context: vscode.ExtensionContext, private readonly state: ChipixState) {
    this.context.subscriptions.push(this.state.changed.event(() => this.postState()));
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, "webview-ui")],
    };
    view.webview.html = webviewHtml(this.context, view.webview, "truthcore.js");
    view.webview.onDidReceiveMessage((msg) => this.handleMessage(msg), undefined, this.context.subscriptions);
    this.postState();
  }

  private async handleMessage(msg: any): Promise<void> {
    try {
      if (msg.type === "ready" || msg.type === "refresh") await this.state.refreshTruthCore(false);
      if (msg.type === "buildTruthCore") await this.state.buildTruthCore();
      if (msg.type === "selectSpec") await this.state.attachSpec();
      if (msg.type === "attachRtlWorkspace") await this.state.attachRtlWorkspace();
    } catch (err: any) {
      const message = errorToMessage(err, "TruthCore action failed");
      vscode.window.showErrorMessage(`TruthCore action failed: ${message}`);
      this.postState(message);
    }
  }

  postState(error = ""): void {
    this.view?.webview.postMessage({
      type: "state",
      state: {
        model: this.state.mentalModel,
        project: this.state.project,
        error,
      },
    });
  }
}
