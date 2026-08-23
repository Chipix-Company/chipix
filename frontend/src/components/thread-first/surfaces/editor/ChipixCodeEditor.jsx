import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import MonacoEditor from "@monaco-editor/react";
import { inferMonacoLanguage } from "./ideEditorUtils";
import { CHIPIX_MONACO_THEMES, setupChipixMonaco } from "./chipixMonacoTheme";
import { formatEditorContent } from "./formatEditorContent";
import { registerIdeInlineCompletion } from "./ideInlineCompletion";
import { registerIdeLint } from "./ideLint";

export default function ChipixCodeEditor({
  value = "",
  onChange,
  filename = "",
  language: languageProp,
  isDarkTheme = true,
  readOnly = false,
  height = "100%",
  onMount,
  showToolbar = true,
  completionOptions = null,
  lintOptions = null,
}) {
  const editorRef = useRef(null);
  const monacoRef = useRef(null);
  const completionHandleRef = useRef(null);
  const lintHandleRef = useRef(null);
  const [minimap, setMinimap] = useState(false);
  const [editorReady, setEditorReady] = useState(false);

  const language = languageProp || inferMonacoLanguage(filename);
  const theme = isDarkTheme ? CHIPIX_MONACO_THEMES.dark : CHIPIX_MONACO_THEMES.light;
  const fileKey = filename || "untitled";

  // Uncontrolled Monaco: parent state updates on every keystroke must not re-sync `value`
  // into the editor — that cancels inline completion sessions (status: aborted, no ghost text).
  const handleEditorChange = useCallback((nextValue) => {
    onChange?.(nextValue ?? "");
  }, [onChange]);

  const handleBeforeMount = useCallback((monaco) => {
    monacoRef.current = monaco;
    setupChipixMonaco(monaco);
  }, []);

  const runFormat = useCallback(async () => {
    const editor = editorRef.current;
    if (!editor || readOnly) return;
    const model = editor.getModel();
    const current = model?.getValue() ?? "";
    const next = await formatEditorContent(current, filename);
    if (next !== current) {
      editor.pushUndoStop();
      editor.executeEdits("chipix-format", [{
        range: model.getFullModelRange(),
        text: next,
      }]);
      editor.pushUndoStop();
      onChange?.(next);
    }
  }, [filename, onChange, readOnly]);

  const handleMount = useCallback((editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    setEditorReady(true);
    editor.addAction({
      id: "chipix-format-document",
      label: "Format document",
      keybindings: [monaco.KeyMod.Shift | monaco.KeyMod.Alt | monaco.KeyCode.KeyF],
      run: () => { void runFormat(); },
    });
    onMount?.(editor, monaco);
  }, [onMount, runFormat]);

  useEffect(() => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    if (!editorReady || !editor || !monaco) return undefined;

    completionHandleRef.current?.dispose?.();
    completionHandleRef.current = null;

    if (completionOptions?.enabled && !readOnly) {
      completionHandleRef.current = registerIdeInlineCompletion(monaco, editor, {
        ...completionOptions,
        monacoLanguage: language,
        filename,
      });
    }

    lintHandleRef.current?.dispose?.();
    lintHandleRef.current = null;

    if (lintOptions?.enabled && !readOnly) {
      lintHandleRef.current = registerIdeLint(monaco, editor, {
        ...lintOptions,
        filename,
        getContent: () => editor.getValue(),
      });
    }

    return () => {
      completionHandleRef.current?.dispose?.();
      completionHandleRef.current = null;
      lintHandleRef.current?.dispose?.();
      lintHandleRef.current = null;
    };
  }, [editorReady, readOnly, language, filename, completionOptions?.enabled, completionOptions?.projectId, lintOptions?.enabled, lintOptions?.projectId]);

  useEffect(() => {
    completionHandleRef.current?.updateOptions?.(completionOptions);
  }, [completionOptions]);

  useEffect(() => {
    lintHandleRef.current?.updateOptions?.({
      ...lintOptions,
      filename,
      getContent: () => editorRef.current?.getValue?.() ?? "",
    });
  }, [lintOptions, filename]);

  const completionEnabled = Boolean(completionOptions?.enabled && !readOnly);

  const monacoOptions = useMemo(() => ({
    readOnly,
    fontFamily: '"JetBrains Mono", "Geist Mono", ui-monospace, monospace',
    fontSize: 13.5,
    lineHeight: 21,
    fontLigatures: true,
    minimap: { enabled: minimap },
    scrollBeyondLastLine: false,
    wordWrap: "off",
    wrappingIndent: "same",
    automaticLayout: true,
    padding: { top: 12, bottom: 12 },
    renderLineHighlight: "line",
    smoothScrolling: true,
    cursorBlinking: "smooth",
    cursorSmoothCaretAnimation: "on",
    bracketPairColorization: { enabled: true },
    guides: { indentation: true, bracketPairs: true },
    inlineSuggest: {
      enabled: completionEnabled,
      mode: "prefix",
      showToolbar: "always",
    },
    quickSuggestions: false,
    suggestOnTriggerCharacters: false,
    scrollbar: {
      verticalScrollbarSize: 10,
      horizontalScrollbarSize: 10,
    },
  }), [readOnly, minimap, completionEnabled]);

  return (
    <div className="tf-code-editor">
      {showToolbar ? (
        <div className="tf-code-editor-toolbar">
          <span className="tf-code-editor-lang">{language}</span>
          <span className="tf-code-editor-spacer" />
          <button
            type="button"
            className={`tf-code-editor-tool${minimap ? " on" : ""}`}
            title="Toggle minimap"
            onClick={() => setMinimap((v) => !v)}
          >
            Map
          </button>
        </div>
      ) : null}
      <div className="tf-code-editor-surface">
        <MonacoEditor
          key={fileKey}
          height={height}
          language={language}
          path={filename || undefined}
          theme={theme}
          defaultValue={value}
          onChange={handleEditorChange}
          beforeMount={handleBeforeMount}
          onMount={handleMount}
          options={monacoOptions}
        />
      </div>
    </div>
  );
}
