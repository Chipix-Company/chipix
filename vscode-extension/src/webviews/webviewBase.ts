import * as vscode from "vscode";

export function webviewHtml(
  context: vscode.ExtensionContext,
  webview: vscode.Webview,
  scriptName: "truthcore.js" | "verification.js",
): string {
  const nonce = String(Date.now());
  const css = webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, "webview-ui", "src", "styles.css"));
  const script = webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, "webview-ui", "src", scriptName));
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';" />
  <link href="${css}" rel="stylesheet" />
</head>
<body>
  <main id="app" class="shell"></main>
  <script nonce="${nonce}" src="${script}"></script>
</body>
</html>`;
}
