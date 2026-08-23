import React from "react";
import ChipixCodeEditor from "./ChipixCodeEditor";
import PdfDocumentViewer from "./PdfDocumentViewer";
import { getEditorFileKind } from "./ideEditorUtils";

export default function ArtifactDocumentPane({
  file,
  isDarkTheme = true,
  readOnly = false,
  onChange,
  onMount,
  completionOptions = null,
  lintOptions = null,
}) {
  if (!file) return null;

  const kind = file.kind || getEditorFileKind(file.name);

  if (kind === "pdf") {
    return (
      <PdfDocumentViewer
        source={file.pdfBlob || null}
        fileName={file.name}
        extractedText={file.content || ""}
        isDarkTheme={isDarkTheme}
      />
    );
  }

  return (
    <ChipixCodeEditor
      value={file.content || ""}
      onChange={onChange}
      filename={file.name}
      language={file.language}
      isDarkTheme={isDarkTheme}
      readOnly={readOnly}
      onMount={onMount}
      completionOptions={completionOptions}
      lintOptions={lintOptions}
    />
  );
}
