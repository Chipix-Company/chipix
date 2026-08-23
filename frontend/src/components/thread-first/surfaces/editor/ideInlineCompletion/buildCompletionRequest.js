import { backendCompletionLanguage } from "../ideEditorUtils";

export function buildCompletionRequest({
  prefix,
  suffix,
  filepath,
  filename,
  language,
  projectId,
  rtlArtifactId,
  specArtifactId,
  mentalModelRevisionId,
  cursorLine,
  cursorColumn,
  context = {},
}) {
  const backendLanguage = backendCompletionLanguage(filename || filepath, language);
  return {
    language: backendLanguage,
    mode: "standard",
    temperature: 0.1,
    max_tokens: 64,
    segments: {
      prefix: prefix || "",
      suffix: suffix || "",
      filepath: filepath || filename || "",
      language: backendLanguage,
      project_id: projectId || undefined,
      rtl_artifact_id: rtlArtifactId || undefined,
      spec_artifact_id: specArtifactId || undefined,
      mental_model_revision_id: mentalModelRevisionId || undefined,
      cursor_line: cursorLine,
      cursor_column: cursorColumn,
      declarations: context.declarations || [],
      relevant_snippets_from_changed_files:
        context.relevant_snippets_from_changed_files || [],
      relevant_snippets_from_recently_opened_files:
        context.relevant_snippets_from_recently_opened_files || [],
    },
    debug_options: {
      disable_rag: true,
    },
  };
}
