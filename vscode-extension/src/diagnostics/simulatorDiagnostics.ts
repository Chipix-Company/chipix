import * as vscode from "vscode";

export class SimulatorDiagnostics {
  private readonly simulatorCollection = vscode.languages.createDiagnosticCollection("chipix-simulator");
  private readonly syntaxCollection = vscode.languages.createDiagnosticCollection("chipix-svls");

  dispose(): void {
    this.simulatorCollection.dispose();
    this.syntaxCollection.dispose();
  }

  async publishFromAnalysis(analysis: any): Promise<void> {
    this.simulatorCollection.clear();
    const diagnostics = Array.isArray(analysis?.diagnostics) ? analysis.diagnostics : [];
    const byFile = new Map<string, vscode.Diagnostic[]>();

    for (const diag of diagnostics) {
      const name = String(diag.file || "").split(/[\\/]/).pop();
      if (!name) continue;
      const range = new vscode.Range(
        Math.max(0, Number(diag.line || 1) - 1),
        Math.max(0, Number(diag.column || 1) - 1),
        Math.max(0, Number(diag.line || 1) - 1),
        Math.max(1, Number(diag.column || 1)),
      );
      const severity = diag.severity === "warning" ? vscode.DiagnosticSeverity.Warning : vscode.DiagnosticSeverity.Error;
      const item = new vscode.Diagnostic(range, `${diag.code || "UVM"}: ${diag.message || diag.root_cause || "Simulator diagnostic"}`, severity);
      item.source = "Chipix";
      byFile.set(name, [...(byFile.get(name) || []), item]);
    }

    for (const [name, items] of byFile) {
      const matches = await vscode.workspace.findFiles(`**/${name}`, "{**/node_modules/**,**/.git/**}", 5);
      for (const uri of matches) {
        this.simulatorCollection.set(uri, items);
      }
    }
  }

  publishLintResult(uri: vscode.Uri, result: any): void {
    const diagnostics = (Array.isArray(result?.diagnostics) ? result.diagnostics : []).map((diag: any) => {
      const startLine = Math.max(0, Number(diag.line || 0));
      const startColumn = Math.max(0, Number(diag.col || 0));
      const endLine = Math.max(startLine, Number(diag.endLine ?? startLine));
      const endColumn = Math.max(startColumn + 1, Number(diag.endCol ?? startColumn + 1));
      const item = new vscode.Diagnostic(
        new vscode.Range(startLine, startColumn, endLine, endColumn),
        String(diag.message || "SystemVerilog syntax issue"),
        lintSeverity(diag.severity),
      );
      item.source = "Chipix svls";
      item.code = String(diag.rule || "svls");
      return item;
    });
    this.syntaxCollection.set(uri, diagnostics);
  }

  clearLintDiagnostics(): void {
    this.syntaxCollection.clear();
  }
}

function lintSeverity(raw: unknown): vscode.DiagnosticSeverity {
  const severity = String(raw || "").toLowerCase();
  if (severity === "error" || severity === "1") return vscode.DiagnosticSeverity.Error;
  if (severity === "information" || severity === "info" || severity === "3") {
    return vscode.DiagnosticSeverity.Information;
  }
  if (severity === "hint" || severity === "4") return vscode.DiagnosticSeverity.Hint;
  return vscode.DiagnosticSeverity.Warning;
}
