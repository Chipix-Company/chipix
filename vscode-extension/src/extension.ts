import * as vscode from "vscode";
import { registerCommands } from "./commands/registerCommands";
import { SimulatorDiagnostics } from "./diagnostics/simulatorDiagnostics";
import { ChipixState } from "./state/chipixState";
import { registerTreeViews } from "./views/treeProviders";
import { TruthCoreViewProvider } from "./webviews/truthCoreView";
import { VerificationViewProvider } from "./webviews/verificationView";

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const state = new ChipixState(context);
  const diagnostics = new SimulatorDiagnostics();

  context.subscriptions.push(
    diagnostics,
    state.output,
    state.truthCoreOutput,
    state.verificationOutput,
    state.simulatorOutput,
    state.repairOutput,
  );

  registerCommands(context, state, diagnostics);
  registerTreeViews(context, state);

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("chipix.truthCore", new TruthCoreViewProvider(context, state), {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.window.registerWebviewViewProvider("chipix.verification", new VerificationViewProvider(context, state, diagnostics), {
      webviewOptions: { retainContextWhenHidden: true },
    }),
  );

  await state.checkBackend(false);
  if (state.projectId) {
    await state.refreshAll().catch(() => undefined);
  }
}

export function deactivate(): void {}
