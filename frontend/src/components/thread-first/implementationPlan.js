/**
 * Unified implementation plan shape for design (chipix:plan) and verification (staged API).
 */

export const IMPLEMENTATION_PLAN_ACTIONS = [
  { id: "approve", label: "Implement", kind: "primary" },
  {
    id: "refine",
    label: "Improve plan",
    takesInput: true,
    inputPlaceholder: "What should change in this plan?",
  },
];

const STRATEGY_LABELS = {
  unitsim: "Unit simulation",
  formal: "Formal verification",
  uvm: "UVM environment",
  all: "All strategies",
};

function asStringList(value) {
  if (!value) return [];
  if (Array.isArray(value)) {
    return value.map((v) => humanizePlanItem(v)).filter(Boolean);
  }
  return [humanizePlanItem(value)].filter(Boolean);
}

function compactText(value, maxChars = 320) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length > maxChars) return `${text.slice(0, maxChars - 3).trimEnd()}...`;
  return text;
}

function sourceRefLabel(sourceRef) {
  if (!sourceRef || typeof sourceRef !== "object") return "";
  const file = String(sourceRef.file || sourceRef.path || "").trim();
  const line = Number(sourceRef.line || 0);
  if (!file) return "";
  return line > 0 ? `${file}:${line}` : file;
}

function firstText(item, fields, maxChars = 320) {
  for (const field of fields) {
    if (item?.[field]) {
      const text = compactText(item[field], maxChars);
      if (text) return text;
    }
  }
  return "";
}

function humanizePlanItem(value) {
  if (!value) return "";
  if (Array.isArray(value)) {
    return value.map((item) => humanizePlanItem(item)).filter(Boolean).join("; ");
  }
  if (typeof value === "object") {
    const question = firstText(value, ["question"], 360);
    const description = firstText(value, ["description", "text", "summary", "risk", "message", "reason"], 360);
    const context = firstText(value, ["context", "module", "module_name", "section"], 120);
    const kind = firstText(value, ["type", "category", "severity", "id", "name"], 120);
    const source = sourceRefLabel(value.source_ref || value.sourceRef);

    let text = "";
    if (question) {
      text = `${context ? `${context}: ` : ""}${question}`;
      if (value.blocking) text = `Blocking question: ${text}`;
    } else if (kind && description) {
      text = `${kind}: ${description}`;
    } else if (context && description) {
      text = `${context}: ${description}`;
    } else {
      const ignored = new Set([
        "source_ref",
        "sourceRef",
        "artifact_id",
        "answered",
        "answer",
        "suggested_answer",
        "blocking",
        "line",
        "page",
        "excerpt",
      ]);
      text = Object.entries(value)
        .filter(([key, raw]) => !ignored.has(key) && raw != null && raw !== "" && typeof raw !== "object")
        .map(([key, raw]) => `${key}: ${compactText(raw, 160)}`)
        .join("; ");
    }
    return source && text ? `${text} (source: ${source})` : text;
  }
  return compactText(value);
}

const PLAN_NOISE_HEADINGS = new Set([
  "technical requirement",
  "technical requirements",
  "project management need",
  "project management needs",
  "functional requirement",
  "functional requirements",
  "verification requirement",
  "verification requirements",
  "design requirement",
  "design requirements",
  "implementation requirement",
  "implementation requirements",
  "requirements",
  "overview",
  "introduction",
  "conclusion",
  "references",
  "appendix",
  "table of contents",
]);

function isNoisyPlanLine(line) {
  const cleaned = String(line || "").replace(/\s+/g, " ").trim();
  if (!cleaned) return false;
  const withoutReq = cleaned.replace(/^REQ-\d+\s*:\s*/i, "").trim();
  const lowered = withoutReq.toLowerCase().replace(/[ .:-]+$/g, "");
  if (PLAN_NOISE_HEADINGS.has(lowered)) return true;
  if (/\.{3,}\s*\d+\s*$/.test(withoutReq)) return true;
  const headingPage = withoutReq.match(/^([A-Za-z][A-Za-z0-9 /\-&]{2,80})\s+\d{1,3}$/);
  if (headingPage && PLAN_NOISE_HEADINGS.has(headingPage[1].toLowerCase().trim())) return true;
  return false;
}

function normalizePlanSummaryMarkdown(value) {
  const raw = String(value || "").replace(/\r\n/g, "\n").trim();
  if (!raw) return "";

  const out = [];
  const pushBlank = () => {
    if (out.length > 0 && out[out.length - 1] !== "") out.push("");
  };

  for (const originalLine of raw.split("\n")) {
    const line = originalLine.trimEnd();
    const trimmed = line.trim();
    if (!trimmed) {
      pushBlank();
      continue;
    }

    const withoutBullet = trimmed.replace(/^[-*]\s+/, "").trim();
    if (isNoisyPlanLine(withoutBullet)) continue;

    const boldHeading = trimmed.match(/^\*\*([^*]+)\*\*$/);
    if (boldHeading) {
      pushBlank();
      out.push(`### ${boldHeading[1].trim()}`);
      continue;
    }

    if (/^#{1,6}\s+/.test(trimmed)) {
      pushBlank();
      out.push(trimmed);
      continue;
    }

    out.push(line);
  }

  return out.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

function normalizeSteps(rawSteps) {
  if (!Array.isArray(rawSteps)) return [];
  return rawSteps
    .map((s, i) => {
      if (typeof s === "string") {
        const label = s.trim();
        if (!label) return null;
        return { id: `step-${i}`, label, state: "todo" };
      }
      if (!s || typeof s !== "object") return null;
      const label = String(s.label || s.name || s.title || "").trim();
      if (!label) return null;
      const deliverables = asStringList(s.deliverables || s.files);
      const whyParts = [s.why, s.description].filter(Boolean).map(String);
      if (deliverables.length) {
        whyParts.push(`Deliverables: ${deliverables.join(", ")}`);
      }
      return {
        id: String(s.id || `step-${i}`),
        label,
        why: whyParts.join(" · ") || undefined,
        state: s.state || "todo",
        deliverables,
      };
    })
    .filter(Boolean);
}

/**
 * Normalize chipix:plan fence JSON or partial payloads → PlanCard props.
 */
export function normalizePlanPayload(raw, options = {}) {
  const source = options.source || raw?._source || "rtl-design";
  const phase = raw?.phase === "verification" ? "verification" : "design";

  const title = String(
    raw?.title
    || (phase === "verification" ? "Verification plan" : "Implementation plan"),
  ).trim();

  const summary = normalizePlanSummaryMarkdown(
    raw?.summary || raw?.understood || raw?.description || "",
  );

  const sub = raw?.sub ? String(raw.sub).trim() : undefined;

  const assumptions = asStringList(raw?.assumptions);
  const constraints = asStringList(raw?.constraints);
  const risks = asStringList(raw?.risks || raw?.open_items);

  let steps = normalizeSteps(raw?.steps);
  if (!steps.length && Array.isArray(raw?.files)) {
    steps = raw.files.map((f, i) => ({
      id: `file-${i}`,
      label: String(f),
      state: "todo",
    }));
  }

  const planId = raw?.plan_id || raw?.planId || raw?.id || null;
  const planDocument = raw?.planDocument || raw?.plan_document || null;
  const actions = Array.isArray(raw?.actions) && raw.actions.length
    ? [...raw.actions]
    : [...IMPLEMENTATION_PLAN_ACTIONS];
  if (planDocument?.artifactId && !actions.some((a) => a.id === "openPlanMarkdown")) {
    actions.unshift({
      id: "openPlanMarkdown",
      label: "Open Markdown plan",
      kind: "secondary",
    });
  }

  return {
    phase,
    title,
    sub,
    summary: summary || undefined,
    assumptions,
    constraints,
    risks,
    steps,
    planId,
    planDocument,
    actions,
    superseded: Boolean(raw?.superseded),
    refineLoading: Boolean(raw?.refineLoading),
    _source: source,
  };
}

function verificationTacticalSteps(verificationType, plan) {
  if (!plan || typeof plan !== "object") return [];
  const steps = [];
  if (plan.unitsim) {
    const count = plan.unitsim.total_scenarios || plan.unitsim.scenarios?.length || 0;
    steps.push({
      id: "unitsim",
      label: `Unit simulation — ${count} scenario${count === 1 ? "" : "s"}`,
      state: "now",
      why: (plan.unitsim.scenarios || [])
        .slice(0, 3)
        .map((s) => s.name)
        .filter(Boolean)
        .join(" · ") || undefined,
    });
  }
  if (plan.formal) {
    const props = plan.formal.property_count ?? plan.formal.properties?.length ?? 0;
    steps.push({
      id: "formal",
      label: `Formal — ${props} propert${props === 1 ? "y" : "ies"}`,
      state: "now",
      why: (plan.formal.properties || [])
        .slice(0, 3)
        .map((p) => (typeof p === "string" ? p : p.name))
        .filter(Boolean)
        .join(" · ") || undefined,
    });
  }
  if (plan.uvm) {
    const agents = plan.uvm.agents?.length || 0;
    const seqs = plan.uvm.sequences?.length || 0;
    const cov = plan.uvm.coverage_points?.length || 0;
    steps.push({
      id: "uvm",
      label: `UVM — ${agents} agent${agents === 1 ? "" : "s"} · ${seqs} sequence${seqs === 1 ? "" : "s"} · ${cov} coverage points`,
      state: "now",
      why: plan.uvm.top_module ? `Top: ${plan.uvm.top_module}` : undefined,
    });
  }
  if (!steps.length) {
    steps.push({
      id: verificationType || "plan",
      label: "Plan ready for review",
      state: "now",
    });
  }
  return steps;
}

/**
 * Build PlanCard payload from staged verification API plan + optional mental model context.
 */
export function planPayloadFromVerificationApi(plan, verificationType, mentalModel = null) {
  const presentation = plan?.presentation && typeof plan.presentation === "object"
    ? plan.presentation
    : {};

  const vtype = verificationType || plan?.verification_type || "all";
  const label = STRATEGY_LABELS[vtype] || vtype;
  const top =
    plan?.uvm?.top_module
    || plan?.unitsim?.module_name
    || plan?.formal?.module_name
    || plan?.module_name
    || plan?.top_module
    || mentalModel?.top_module
    || mentalModel?.design?.top_module
    || null;

  let summary = normalizePlanSummaryMarkdown(presentation.summary || "");
  if (!summary && mentalModel) {
    const parts = [];
    const description = mentalModel.description || mentalModel.design?.description || mentalModel.top_description;
    if (description) parts.push(description);
    if (mentalModel.top_module || mentalModel.design?.top_module) {
      parts.push(`Top module: ${mentalModel.top_module || mentalModel.design.top_module}`);
    }
    summary = normalizePlanSummaryMarkdown(parts.join(" · "));
  }
  if (!summary) {
    summary = `Verification plan for ${label}${top ? ` on ${top}` : ""}.`;
  }

  const planArtifact =
    plan?.plan_markdown_artifact && typeof plan.plan_markdown_artifact === "object"
      ? plan.plan_markdown_artifact
      : null;
  const planDocument = (plan?.plan_markdown_artifact_id || planArtifact?.artifact_id)
    ? {
        artifactId: plan?.plan_markdown_artifact_id || planArtifact?.artifact_id,
        name: plan?.plan_markdown_filename || planArtifact?.filename || "verification_plan.md",
        filename: plan?.plan_markdown_filename || planArtifact?.filename || "verification_plan.md",
        planGroup: plan?.plan_markdown_group || planArtifact?.plan_group || null,
        revision: planArtifact?.revision || null,
        language: "markdown",
      }
    : null;

  const assumptions = asStringList(presentation.assumptions);
  const constraints = asStringList(presentation.constraints);
  const risks = asStringList(presentation.risks || presentation.warnings);

  const readinessWarnings = presentation.readiness_warnings;
  if (Array.isArray(readinessWarnings)) {
    for (const w of readinessWarnings) {
      const t = String(w || "").trim();
      if (t && !risks.includes(t)) risks.push(t);
    }
  }

  return normalizePlanPayload({
    phase: "verification",
    title: "Verification plan",
    sub: top ? `${label} · ${top}` : label,
    summary,
    assumptions,
    constraints,
    risks,
    steps: verificationTacticalSteps(vtype, plan),
    planId: plan?.mental_model_revision_id || plan?.id || null,
    planDocument,
    _source: "staged",
  });
}

export function implementationPlanStorageKey(projectId, threadId) {
  if (!projectId || !threadId) return null;
  return `chipverify.implementationPlan:${projectId}:${threadId}`;
}

export function loadImplementationPlanState(projectId, threadId) {
  const key = implementationPlanStorageKey(projectId, threadId);
  if (!key || typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

export function saveImplementationPlanState(projectId, threadId, state) {
  const key = implementationPlanStorageKey(projectId, threadId);
  if (!key || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify({ ...state, updatedAt: Date.now() }));
  } catch {
    // quota / private mode
  }
}

export function clearImplementationPlanState(projectId, threadId) {
  const key = implementationPlanStorageKey(projectId, threadId);
  if (!key || typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // ignore
  }
}
