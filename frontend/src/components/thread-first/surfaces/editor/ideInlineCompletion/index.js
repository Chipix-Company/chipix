import { isCompletableFile, isInlineCompletionEnabled } from "../ideEditorUtils";
import { CompletionClientService } from "./CompletionClientService";
import { extractPrefixSuffix } from "./prefixSuffix";

/**
 * Register Monaco inline completion provider — Tabby-aligned flow:
 * record keystroke → debounce → cancellation check → fetch (mutex + Monaco token).
 */
export function registerIdeInlineCompletion(monaco, editor, options = {}) {
  if (!monaco?.languages?.registerInlineCompletionsProvider || !editor) {
    options.debug?.skip("Monaco inline provider unavailable");
    return { dispose: () => {}, updateOptions: () => {} };
  }

  const service = new CompletionClientService({
    ...options,
    enabled: options.enabled !== false && isInlineCompletionEnabled(),
    getActiveFile: options.getActiveFile,
    getIdeFiles: options.getIdeFiles,
    getRevisionMap: options.getRevisionMap,
    debug: options.debug,
  });

  const provider = {
    displayName: "ChipVerifyTabComplete",
    provideInlineCompletions: async (model, position, context, token) => {
      try {
        if (context?.includeInlineCompletions === false) {
          service.debug?.skip("Monaco excluded inline completions");
          return { items: [] };
        }

        service.debouncer.recordKeystroke();

        const activeFile = service.getActiveFile?.();
        const filename = activeFile?.name || model?.uri?.path?.split("/").pop() || "";
        if (!isCompletableFile(filename)) {
          service.debug?.skip(`Not RTL file (${filename || "unknown"})`);
          return { items: [] };
        }

        const triggerKind = context?.triggerKind ?? 0;
        const { prefix, suffix } = extractPrefixSuffix(model, position);

        service.debug?.trigger({
          line: position.lineNumber,
          column: position.column,
          file: filename,
          triggerKind,
        });

        const debounceMs = await service.debouncer.wait(
          triggerKind,
          prefix,
          suffix,
          (delayMs) => service.debug?.debounce(delayMs),
        );

        if (debounceMs < 0 || token.isCancellationRequested) {
          return { items: [] };
        }

        const item = await service.fetchCompletion(model, position, monaco, {
          monacoToken: token,
          prefix,
          suffix,
        });

        if (!item || token.isCancellationRequested) {
          return { items: [] };
        }

        return { items: [item], enableForwardStableCandidates: true };
      } catch (err) {
        service.debug?.error(err?.message || String(err));
        return { items: [] };
      }
    },

    disposeInlineCompletions: () => {},

    handleItemDidShow: () => {},
  };

  const monacoLanguage = options.monacoLanguage
    || editor.getModel?.()?.getLanguageId?.()
    || "systemverilog";
  const registration = monaco.languages.registerInlineCompletionsProvider(
    { language: monacoLanguage },
    provider,
  );

  const acceptSub = editor.onDidAcceptInlineCompletion?.(() => {
    if (service._lastCompletionId) {
      void service.telemetry.select(service._lastCompletionId, {
        choiceText: "",
        selectKind: "tab",
        elapsedMs: Date.now() - service._lastShownAt,
      });
    }
  });

  const keySub = editor.onKeyDown?.((e) => {
    if (e.keyCode === monaco.KeyCode.Escape && service._lastCompletionId) {
      void service.telemetry.dismiss(service._lastCompletionId, {
        elapsedMs: Date.now() - service._lastShownAt,
      });
    }
  });

  return {
    dispose: () => {
      service.dispose();
      registration.dispose();
      acceptSub?.dispose?.();
      keySub?.dispose?.();
    },
    updateOptions: (next) => service.updateOptions(next),
    invalidateFile: (filepath) => service.invalidateFile(filepath),
    setEnabled: (value) => service.updateOptions({ enabled: value }),
  };
}
