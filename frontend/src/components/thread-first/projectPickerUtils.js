const GRADIENTS = [
  "linear-gradient(135deg,#c2a06e,#8a6a3e)",
  "linear-gradient(135deg,#7a8da0,#4a5c70)",
  "linear-gradient(135deg,#a07e7e,#705252)",
  "linear-gradient(135deg,#8aa07e,#52704e)",
  "linear-gradient(135deg,#a08e7e,#705a52)",
  "linear-gradient(135deg,#7e8aa0,#4e5a70)",
  "linear-gradient(135deg,#9a7ea0,#5a4e70)",
];

export function projectInitials(name = "") {
  return String(name)
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() || "")
    .join("") || "·";
}

export function gradientForProject(id = "") {
  const idx = Math.abs(
    Array.from(String(id)).reduce((a, c) => a + c.charCodeAt(0), 0),
  ) % GRADIENTS.length;
  return GRADIENTS[idx];
}

export function formatProjectWhen(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "";
    const diff = Date.now() - d.getTime();
    if (diff < 60000) return "Just now";
    if (diff < 3600000) return `${Math.round(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.round(diff / 3600000)}h ago`;
    if (diff < 604800000) return `${Math.round(diff / 86400000)}d ago`;
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

/** Secondary line — time, slug, or id tail so duplicate names stay distinct. */
export function projectMetaLine(project) {
  if (!project) return "";
  const parts = [];
  const when = formatProjectWhen(project.updated_at || project.created_at);
  if (when) parts.push(when);

  const slug = String(project.slug || "").trim();
  const nameSlug = String(project.name || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  if (slug && slug !== nameSlug) {
    parts.push(slug);
  } else {
    const id = String(project.id || "");
    const tail = id.length >= 4 ? id.slice(-4) : id;
    if (tail) parts.push(`#${tail}`);
  }

  const desc = String(project.description || "").trim();
  if (desc && parts.length < 2) {
    parts.push(desc.length > 36 ? `${desc.slice(0, 33)}…` : desc);
  }

  return parts.join(" · ");
}

export function filterProjects(projects = [], query = "") {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return projects;
  return projects.filter((p) => {
    const hay = [
      p.name,
      p.slug,
      p.description,
      p.id,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return hay.includes(q);
  });
}
