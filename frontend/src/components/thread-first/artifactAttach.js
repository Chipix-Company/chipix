/** Shared helpers for project artifact uploads + composer chips. */

export const ARTIFACT_KIND = {
  SPEC: "spec",
  RTL: "rtl",
  RTL_FOLDER: "rtl-folder",
  CONTEXT: "context",
};

export function artifactDisplayName(a) {
  if (!a) return "file";
  return (
    String(a.memberPath || a.filename || a.metadata?.relative_path || a.metadata?.path || a.name || "")
    || String(a.id || "").slice(0, 8)
  );
}

export function artifactKindFromRecord(a) {
  const t = String(a?.artifact_type || "").toLowerCase();
  if (t === "spec" || t === "specification") return ARTIFACT_KIND.SPEC;
  if (t === "rtl" || t === "generated") return ARTIFACT_KIND.RTL;
  return ARTIFACT_KIND.CONTEXT;
}

export function kindLabel(kind) {
  if (kind === ARTIFACT_KIND.SPEC) return "Specification";
  if (kind === ARTIFACT_KIND.RTL_FOLDER) return "RTL folder";
  if (kind === ARTIFACT_KIND.RTL) return "RTL";
  if (kind === ARTIFACT_KIND.CONTEXT) return "Context";
  return "File";
}

export function uploadLabelForFiles(files, kind) {
  const list = Array.isArray(files) ? files : [];
  if (!list.length) return kindLabel(kind);
  if (list.length === 1) {
    return list[0].webkitRelativePath || list[0].name || kindLabel(kind);
  }
  if (kind === ARTIFACT_KIND.RTL_FOLDER) {
    return `RTL folder · ${list.length} files`;
  }
  return `${list.length} files`;
}

export function makeUploadJobId() {
  return `upload-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function rtlProjectMemberPaths(a) {
  const metadata = a?.metadata || {};
  const candidates = [
    metadata.included_workspace_files,
    metadata.included_project_files,
    metadata.included_rtl_files,
    metadata.included_rtl_file_paths,
    metadata.members,
    metadata.analysis?.rtl_files,
    metadata.analysis?.rtl_file_paths,
    a?.content_metadata?.members,
  ];

  const seen = new Set();
  const paths = [];
  candidates.forEach((candidate) => {
    if (!Array.isArray(candidate)) return;
    candidate.forEach((entry) => {
      const raw = typeof entry === "string"
        ? entry
        : entry?.path || entry?.relative_path || entry?.filename || "";
      const normalized = String(raw || "").replace(/\\/g, "/").replace(/^\/+/, "").trim();
      if (!normalized || seen.has(normalized)) return;
      seen.add(normalized);
      paths.push(normalized);
    });
  });
  return paths;
}

const RTL_ARCHIVE_MEMBER_PATTERN = /\.(sv|svh|v|vh|vhd|vhdl)$/i;

export function isRtlProjectArchiveRecord(a) {
  const filename = String(a?.filename || "").toLowerCase();
  const metadata = a?.metadata || {};
  return (
    String(a?.artifact_type || "").toLowerCase() === "rtl"
    && (
      filename.endsWith(".zip")
      || metadata.provided_as === "rtl_project"
      || rtlProjectMemberPaths(a).length > 0
    )
  );
}

export function expandRtlProjectArtifactMembers(a) {
  if (!isRtlProjectArchiveRecord(a)) {
    return a ? [{ ...a, artifactId: a.artifactId || a.id }] : [];
  }

  const members = rtlProjectMemberPaths(a).filter((path) => RTL_ARCHIVE_MEMBER_PATTERN.test(path));
  if (!members.length) {
    return [{ ...a, artifactId: a.artifactId || a.id }];
  }

  return members.map((memberPath) => ({
    ...a,
    id: `${a.id}::${memberPath}`,
    artifactId: a.id,
    memberPath,
    filename: memberPath,
    name: memberPath,
    virtual_member: true,
    metadata: {
      ...(a.metadata || {}),
      relative_path: memberPath,
      archive_artifact_id: a.id,
      archive_filename: a.filename,
    },
  }));
}

export function expandProjectArtifactsForWorkspace(list) {
  return (list || []).flatMap((a) => expandRtlProjectArtifactMembers(a));
}

/** Latest revision per (type, filename) for agent workspace context. */
export function buildProjectArtifactManifest(artifactState, { maxItems = 48 } = {}) {
  const artifacts = artifactState?.artifacts || {};
  const active = artifactState?.active || {};
  const activeSpecId = active.active_spec_artifact_id || null;
  const activeRtlId = active.active_rtl_artifact_id || null;
  const rows = [];
  for (const type of ["spec", "rtl", "generated"]) {
    for (const a of artifacts[type] || []) {
      if (!a?.id) continue;
      const expanded = type === "rtl" ? expandRtlProjectArtifactMembers(a) : [{ ...a, artifactId: a.id }];
      expanded.forEach((row) => {
        rows.push({
          id: row.artifactId || row.id,
          member_path: row.memberPath || null,
          filename: artifactDisplayName(row),
          artifact_type: type,
          revision: row.revision ?? 0,
          source: row.source || "unknown",
          is_active: row.artifactId === activeSpecId || row.artifactId === activeRtlId || row.id === activeSpecId || row.id === activeRtlId,
        });
      });
    }
  }
  rows.sort((a, b) => {
    const ta = String(a.artifact_type).localeCompare(String(b.artifact_type));
    if (ta !== 0) return ta;
    return String(a.filename).localeCompare(String(b.filename));
  });
  const seen = new Set();
  const deduped = [];
  for (const row of rows) {
    const key = `${row.artifact_type}::${row.filename}`;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(row);
    if (deduped.length >= maxItems) break;
  }
  const activeRtl = deduped.filter((r) => r.is_active && r.artifact_type === "rtl").map((r) => r.filename);
  const activeSpec = deduped.filter((r) => r.is_active && r.artifact_type === "spec").map((r) => r.filename);
  return {
    artifacts: deduped,
    has_active_rtl: Boolean(activeRtlId),
    has_active_spec: Boolean(activeSpecId),
    active_rtl_artifact_id: activeRtlId,
    active_spec_artifact_id: activeSpecId,
    active_rtl_filenames: activeRtl,
    active_spec_filenames: activeSpec,
    artifact_count: deduped.length,
  };
}

export function workspaceFilesFromManifest(manifest) {
  const list = manifest?.artifacts || [];
  return list.map((a) => {
    const tag = a.is_active ? "active" : a.artifact_type;
    return `${a.filename} (${tag})`;
  });
}
