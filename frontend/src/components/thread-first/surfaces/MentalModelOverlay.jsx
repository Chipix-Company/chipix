import React, { useCallback, useEffect, useMemo, useState } from "react";
import Icon from "../icons";
import MentalModelGraph from "./MentalModelGraph";
import MentalModelChatDock from "./MentalModelChatDock";
import { synthesiseMentalModel } from "./mentalModelSynthesis";
import { playTick, playChime } from "../useChipixSound";
import useOverlayClose from "../useOverlayClose";
import { buildMentalModel, queryMentalModel } from "../../../api/verificationApi";
import { AnalyticsEvents, captureError, track } from "../../../lib/observability";

/**
 * MentalModelOverlay — "How I understand this design".
 *
 * First-principles port (see docs/userflow/first-principles-feature-port-plan.md §4):
 *   - The graph is the homepage; top tabs are gone.
 *   - A slim aspect rail on the LEFT lists scopes (Overview · Modules · Ports ·
 *     Parameters · Functionalities).
 *   - Selecting a rail item updates the detail strip UNDER the graph (does not
 *     replace it).
 *   - Clicking a node in the graph scopes the rail/strip to that module and
 *     visually highlights the node.
 *   - When no model exists: ONE primary "Build" action — never a generic empty
 *     state.
 */

const ASPECTS = [
  { id: "overview", label: "Overview", Ico: Icon.MmOverview },
  { id: "modules", label: "Modules", Ico: Icon.MmModules },
  { id: "ports", label: "Ports", Ico: Icon.MmPorts },
  { id: "parameters", label: "Parameters", Ico: Icon.Settings },
  { id: "functionalities", label: "Functionalities", Ico: Icon.Activity },
];

// ---- Response normalization (kept compatible with backend's nested shape) ----
function normalize(raw) {
  if (!raw) return null;
  const revision =
    raw.mental_model && typeof raw.mental_model === "object" ? raw.mental_model : raw;
  const content =
    revision.content && typeof revision.content === "object"
      ? revision.content
      : raw.content && typeof raw.content === "object"
        ? raw.content
        : revision;
  const model =
    content.content && typeof content.content === "object" ? content.content : content;
  const design = model.design && typeof model.design === "object" ? model.design : {};
  const projectScan = model.project_scan || design.project_scan || {};

  return {
    ...design,
    ...model,
    id: revision.id || raw.id,
    revision: revision.revision || raw.revision,
    summary_text: revision.summary_text || raw.summary_text,
    created_at: revision.created_at || raw.created_at,
    top_module: model.top_module || design.top_module || "",
    modules: model.modules || design.modules || [],
    ports: model.ports || design.ports || [],
    parameters: model.parameters || design.parameters || [],
    clock_domains: model.clock_domains || design.clock_domains || [],
    sub_instances: model.sub_instances || design.sub_instances || [],
    hierarchy_tree: model.hierarchy_tree || design.hierarchy_tree || {},
    block_models: model.block_models || design.block_models || {},
    requirements: model.requirements || design.requirements || [],
    source_files:
      model.source_files || design.source_files || projectScan.rtl_files || [],
    source_refs: model.source_refs || design.source_refs || [],
    description: model.description || design.description || "",
  };
}

function isEmptyModel(d) {
  if (!d) return true;
  return (
    !d.top_module &&
    (!d.modules || d.modules.length === 0) &&
    (!d.hierarchy_tree || Object.keys(d.hierarchy_tree).length === 0)
  );
}

function moduleNameOf(raw, fallback = "") {
  if (!raw || typeof raw !== "object") return fallback;
  return raw.name || raw.module_name || raw.module || raw.top_module || fallback;
}

function normalizeModuleBlock(name, raw) {
  if (!raw || typeof raw !== "object") return null;
  const design = raw.design && typeof raw.design === "object" ? raw.design : {};
  const block = { ...design, ...raw };
  const moduleName = moduleNameOf(block, name);
  return {
    ...block,
    name: moduleName,
    top_module: block.top_module || moduleName,
    description: block.description || design.description || block.summary || design.summary || "",
  };
}

function moduleMatches(value, moduleName) {
  return String(value || "") === String(moduleName || "");
}

function portBelongsToModule(port, moduleName) {
  if (!port || !moduleName) return false;
  return [
    port.module,
    port.module_name,
    port.parent_module,
    port.owner,
    port.scope,
    port.block,
    port.top_module,
  ].some((value) => moduleMatches(value, moduleName));
}

function selectedModuleBlock(data, selectedNode) {
  if (!data || !selectedNode) return null;
  if (selectedNode === data.top_module) return normalizeModuleBlock(selectedNode, data);

  const blockModels = data.block_models && typeof data.block_models === "object"
    ? data.block_models
    : {};
  const direct = normalizeModuleBlock(selectedNode, blockModels[selectedNode]);
  if (direct) return direct;

  const modules = Array.isArray(data.modules) ? data.modules : [];
  const moduleItem = modules.find((m) => (
    typeof m === "object" && moduleNameOf(m) === selectedNode
  ));
  return normalizeModuleBlock(selectedNode, moduleItem);
}

function portsForModule(data, selectedNode, block = null) {
  if (!data) return [];
  if (!selectedNode || selectedNode === data.top_module) return data.ports || [];
  if (Array.isArray(block?.ports) && block.ports.length > 0) return block.ports;
  return (data.ports || []).filter((p) => portBelongsToModule(p, selectedNode));
}

function parametersForModule(data, selectedNode, block = null) {
  if (!data) return [];
  if (!selectedNode || selectedNode === data.top_module) return data.parameters || [];
  if (Array.isArray(block?.parameters) && block.parameters.length > 0) return block.parameters;
  return (data.parameters || []).filter((p) => portBelongsToModule(p, selectedNode));
}

const GENERIC_REQUIREMENT_HEADINGS = new Set([
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

function requirementTextOf(req) {
  if (req && typeof req === "object") {
    return String(req.text || req.description || "").trim();
  }
  return String(req || "").trim();
}

function isNoiseRequirement(req) {
  const text = requirementTextOf(req).replace(/\s+/g, " ").trim();
  if (!text) return true;
  const lowered = text.toLowerCase().replace(/[ .:-]+$/g, "");
  if (GENERIC_REQUIREMENT_HEADINGS.has(lowered)) return true;
  if (/\.{3,}\s*\d+\s*$/.test(text)) return true;
  const headingPage = text.match(/^([A-Za-z][A-Za-z0-9 /\-&]{2,80})\s+\d{1,3}$/);
  if (headingPage && GENERIC_REQUIREMENT_HEADINGS.has(headingPage[1].toLowerCase().trim())) return true;
  if (/^(chapter|section|table|figure|page)\s+\d+/i.test(text)) return true;
  if (text.split(/\s+/).length <= 4 && !/\b(shall|must|should|will|verify|support|capture|drive|drives|assert|deassert|clear|clears|read|reads|write|writes|return|returns|translate|translates|latch|latches|generate|generates|hold|holds|stall|stalls)\b/i.test(text)) {
    return true;
  }
  return false;
}

function meaningfulRequirements(requirements = []) {
  return (Array.isArray(requirements) ? requirements : []).filter((req) => !isNoiseRequirement(req));
}

function behaviorDescriptionOf(item) {
  if (!item || typeof item !== "object") return "";
  return String(item.description || item.expected_behavior || item.behavior || item.summary || item.name || "").trim();
}

function fallbackModuleDescription(data, selectedNode, ports = []) {
  if (!selectedNode) return "";
  const tree = data?.hierarchy_tree || {};
  const children = tree[selectedNode] || [];
  const parents = Object.entries(tree)
    .filter(([, kids]) => Array.isArray(kids) && kids.includes(selectedNode))
    .map(([name]) => name);
  const parts = [
    `${selectedNode} is ${selectedNode === data?.top_module ? "the top module" : "a submodule"} in this design`,
  ];
  if (parents.length > 0) parts.push(`instantiated under ${parents.join(", ")}`);
  if (children.length > 0) parts.push(`contains ${children.join(", ")}`);
  if (ports.length > 0) parts.push(`exposes ${ports.length} boundary port${ports.length === 1 ? "" : "s"}`);
  return `${parts.join(". ")}.`;
}

export default function MentalModelOverlay({
  open,
  onClose,
  projectId,
  authToken,
  projectName,
  onBuilt,
  onCadenceAction,
  onGraphChatSend,
  graphChatMessages = [],
  cadenceConnected = false,
  hasUvmArtifacts = false,
  graphChatDisabled = false,
  onOpenGraphChatInMainThread,
}) {
  const [data, setData] = useState(null);
  const [aspect, setAspect] = useState("overview");
  const [selectedNode, setSelectedNode] = useState(null);
  const [loading, setLoading] = useState(false);
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const raw = await queryMentalModel(projectId, "overview", authToken);
      setData(normalize(raw));
    } catch (e) {
      // The backend returns 404 when no model exists; treat as "empty".
      const msg = String(e?.message || "");
      if (/404/.test(msg) || /not.?found/i.test(msg)) {
        setData(null);
      } else {
        setError(msg || "Failed to load mental model");
      }
    } finally {
      setLoading(false);
    }
  }, [projectId, authToken]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  // Reset transient UI when re-opening or switching projects.
  useEffect(() => {
    if (!open) {
      setSelectedNode(null);
      setAspect("overview");
    }
  }, [open, projectId]);

  const handleBuild = useCallback(async () => {
    if (!projectId) return;
    setBuilding(true);
    setError(null);
    track(AnalyticsEvents.MENTAL_MODEL_BUILD_STARTED, { project_id: projectId });
    try {
      const result = await buildMentalModel(projectId, {}, authToken);
      await load();
      onBuilt?.(result);
      track(AnalyticsEvents.MENTAL_MODEL_BUILD_COMPLETED, {
        project_id: projectId,
        revision_id: result?.mental_model?.id || result?.id,
      });
      // Soft confirmation chime — model is built, the picture is alive.
      playChime();
    } catch (e) {
      const message = String(e?.message || "Failed to build mental model");
      setError(message);
      track(AnalyticsEvents.MENTAL_MODEL_BUILD_FAILED, {
        project_id: projectId,
        error: message,
      });
      captureError(e, { area: "mental_model.overlay_build", project_id: projectId });
    } finally {
      setBuilding(false);
    }
  }, [projectId, authToken, load, onBuilt]);

  // Aspect / node click handlers with sound — the rail is a primary
  // affordance, the toggle-style tick reinforces that something happened.
  const handleAspectChange = useCallback((next) => {
    if (next !== aspect) {
      playTick({ pitch: "high" });
      setAspect(next);
    }
  }, [aspect]);

  const handleNodeSelect = useCallback((name) => {
    setSelectedNode((prev) => {
      const next = prev === name ? null : name;
      if (next !== prev) playTick({ pitch: next ? "high" : "low" });
      return next;
    });
  }, []);

  const empty = isEmptyModel(data);

  // Narrative synthesis — recomputed when data or scope changes. Cheap.
  const narrative = useMemo(
    () => (empty ? null : synthesiseMentalModel(data, { selectedNode })),
    [data, selectedNode, empty],
  );
  const subtitle = useMemo(() => {
    if (!data) return "My mental model · derived from your description and clarifying answers";
    const bits = [];
    if (Array.isArray(data.source_files) && data.source_files.length > 0) {
      bits.push(
        `derived from ${data.source_files.slice(0, 2).join(", ")}${
          data.source_files.length > 2 ? `, +${data.source_files.length - 2}` : ""
        }`,
      );
    } else if (data.summary_text) {
      bits.push(data.summary_text);
    }
    return bits.length ? bits.join(" · ") : "My mental model";
  }, [data]);

  const { closeButtonProps } = useOverlayClose({ open, onClose, closeOnEsc: false, label: "Close mental model" });

  if (!open) return null;

  return (
    <div
      className="tf-mm-overlay open tf-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Mental model"
    >
      <header className="h tf-overlay-header">
        <div className="tf-mm-h-titles">
          <div className="title">
            How I understand this design
            {projectName ? ` · ${projectName}` : ""}
          </div>
          <div className="sub">{subtitle}</div>
        </div>
        <span className="spacer" />
        {!empty && (
          <button
            type="button"
            className="tf-btn sm"
            onClick={handleBuild}
            disabled={building || loading}
            title="Rebuild from current spec + RTL"
          >
            <Icon.Refresh width="13" height="13" />
            {building ? "Rebuilding…" : "Rebuild"}
          </button>
        )}
        <button {...closeButtonProps}>
          <Icon.Close width="14" height="14" />
        </button>
      </header>

      <div className="body">
        {error && (
          <div className="tf-mm-error" role="alert">
            <Icon.Warning width="14" height="14" />
            <span>{error}</span>
            <button type="button" className="tf-btn sm" onClick={load}>
              Try again
            </button>
          </div>
        )}

        {loading && !data && <LoadingShimmer />}

        {!loading && empty && !error && (
          <EmptyBuildPrompt onBuild={handleBuild} building={building} />
        )}

        {data && !empty && (
          <div className={`tf-mm-shell${aspect !== "overview" ? " aspect-focus" : ""}`}>
            <AspectRail
              active={aspect}
              onChange={handleAspectChange}
              data={data}
              selectedNode={selectedNode}
              onClearScope={() => {
                playTick({ pitch: "low" });
                setSelectedNode(null);
              }}
            />
            <div className="tf-mm-main">
              {narrative ? (
                <NarrativeStrip narrative={narrative} compact={aspect !== "overview"} />
              ) : null}
              <div className={`tf-mm-content-grid${aspect !== "overview" ? " focused" : ""}`}>
                <div className={`tf-mm-graph-pane${aspect !== "overview" ? " compact" : ""}`}>
                  <div className="tf-mm-graph-pane-label">
                    <Icon.Layers width="13" height="13" />
                    <span>Hierarchy</span>
                  </div>
                  <MentalModelGraph
                    data={data}
                    selectedNode={selectedNode}
                    onSelectNode={handleNodeSelect}
                  />
                </div>
                <DetailStrip
                  key={`${aspect}::${selectedNode || "_"}`}
                  aspect={aspect}
                  data={data}
                  selectedNode={selectedNode}
                  focused={aspect !== "overview"}
                  cadenceConnected={cadenceConnected}
                  hasUvmArtifacts={hasUvmArtifacts}
                  onCadenceAction={onCadenceAction}
                />
              </div>
              <MentalModelChatDock
                messages={graphChatMessages}
                disabled={graphChatDisabled}
                onSend={(text) => onGraphChatSend?.(text, { selectedNode, revisionId: data?.id })}
                onOpenInMainThread={onOpenGraphChatInMainThread}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Sub-components ─────────────────────────────────────────────────────────

function NarrativeStrip({ narrative, compact = false }) {
  if (!narrative) return null;
  const { headline, summary, scopedTo, completeness } = narrative;
  const animKey = scopedTo || "_all";
  return (
    <section
      className={`tf-mm-narrative tone-${completeness.tone}${compact ? " compact" : ""}`}
      key={animKey}
      role="region"
      aria-label="What I understand about this design"
    >
      <div className="tf-mm-narrative-head">
        <span className="tf-mm-narrative-glyph" aria-hidden>
          <Icon.Sparkles width="14" height="14" />
        </span>
        <span className="tf-mm-narrative-eyebrow">
          {scopedTo ? "Focused on" : "How I read your design"}
        </span>
        <span className={`tf-mm-confidence tf-mm-confidence-${completeness.tone}`}>
          <span className="tf-mm-confidence-dot" aria-hidden />
          <span className="tf-mm-confidence-label">{completeness.label}</span>
        </span>
      </div>
      {compact ? (
        <p className="tf-mm-narrative-compact-line">{headline}</p>
      ) : (
        <>
          <h2 className="tf-mm-narrative-headline">{headline}</h2>
          <p className="tf-mm-narrative-summary">{summary}</p>
        </>
      )}
    </section>
  );
}

function LoadingShimmer() {
  return (
    <div className="tf-mm-loading">
      <div className="tf-mm-shimmer tf-mm-shimmer-graph" />
      <div className="tf-mm-shimmer tf-mm-shimmer-line" />
      <div className="tf-mm-shimmer tf-mm-shimmer-line short" />
      <div className="tf-thinking" aria-live="polite">
        <span>Reading spec + RTL</span>
        <span className="dots">
          <span /><span /><span />
        </span>
      </div>
    </div>
  );
}

function EmptyBuildPrompt({ onBuild, building }) {
  return (
    <div className="tf-mm-empty">
      <p className="tf-mm-empty-lead">
        I haven&rsquo;t built a mental model for this project yet.
        I&rsquo;ll read the spec, scan the RTL, and lay out how the modules
        fit together.
      </p>
      <button
        type="button"
        className="tf-btn primary"
        onClick={onBuild}
        disabled={building}
      >
        {building ? (
          <>
            <Icon.Refresh width="13" height="13" />
            Building…
          </>
        ) : (
          <>
            <Icon.Sparkles width="13" height="13" />
            Build the mental model from the current spec + RTL
          </>
        )}
      </button>
    </div>
  );
}

function aspectCount(id, data) {
  if (!data) return null;
  switch (id) {
    case "modules": return (data.modules || []).length || null;
    case "ports": return (data.ports || []).length || null;
    case "parameters": return (data.parameters || []).length || null;
    case "functionalities": return meaningfulRequirements(data.requirements || []).length || null;
    default: return null;
  }
}

function AspectRail({ active, onChange, data, selectedNode, onClearScope }) {
  return (
    <nav className="tf-mm-rail" aria-label="Aspect rail">
      {ASPECTS.map((a) => {
        const isActive = a.id === active;
        const Ico = a.Ico;
        const count = aspectCount(a.id, data);
        return (
          <button
            key={a.id}
            type="button"
            className={`tf-mm-rail-item${isActive ? " active" : ""}`}
            aria-current={isActive ? "page" : undefined}
            onClick={() => onChange(a.id)}
          >
            <span className="tf-mm-rail-ico" aria-hidden>
              <Ico width="15" height="15" strokeWidth={2} />
            </span>
            <span className="tf-mm-rail-label">{a.label}</span>
            {count != null ? (
              <span className="tf-mm-rail-count" aria-hidden>{count}</span>
            ) : null}
          </button>
        );
      })}
      {selectedNode && (
        <>
          <div className="tf-mm-rail-divider" />
          <div className="tf-mm-rail-scope">
            <span className="tf-mm-rail-scope-label">Scoped to</span>
            <span className="tf-mm-rail-scope-name">{selectedNode}</span>
            <button
              type="button"
              className="tf-mm-rail-scope-clear"
              onClick={onClearScope}
              aria-label="Clear scope"
            >
              <Icon.Close width="11" height="11" />
            </button>
          </div>
        </>
      )}
    </nav>
  );
}

const ASPECT_LABELS = {
  overview: "Overview",
  modules: "Modules",
  ports: "Ports",
  parameters: "Parameters",
  functionalities: "Functionalities",
};

function DetailStrip({
  aspect,
  data,
  selectedNode,
  focused,
  cadenceConnected = false,
  hasUvmArtifacts = false,
  onCadenceAction,
}) {
  const aspectMeta = ASPECTS.find((a) => a.id === aspect);
  const AspectIco = aspectMeta?.Ico || Icon.MmOverview;
  return (
    <section
      className={`tf-mm-strip${focused ? " focused" : ""}`}
      aria-label={`${aspect} detail`}
    >
      {focused ? (
        <header className="tf-mm-strip-head">
          <span className="tf-mm-strip-head-ico" aria-hidden>
            <AspectIco width="14" height="14" strokeWidth={2} />
          </span>
          <span className="tf-mm-strip-head-title">{ASPECT_LABELS[aspect] || aspect}</span>
          {selectedNode ? (
            <span className="tf-mm-strip-head-scope">{selectedNode}</span>
          ) : null}
        </header>
      ) : null}
      <div className={focused ? "tf-mm-strip-body" : "tf-mm-strip-body tf-mm-strip-body-inline"}>
      {aspect === "overview" && (
        <OverviewDetail data={data} selectedNode={selectedNode} />
      )}
      {aspect === "modules" && (
        <ModulesDetail data={data} selectedNode={selectedNode} />
      )}
      {aspect === "ports" && (
        <PortsDetail data={data} selectedNode={selectedNode} />
      )}
      {aspect === "parameters" && (
        <ParametersDetail data={data} selectedNode={selectedNode} />
      )}
      {aspect === "functionalities" && <FunctionalitiesDetail data={data} selectedNode={selectedNode} />}
      </div>
      {(onCadenceAction && hasUvmArtifacts) ? (
        <footer className="tf-mm-cadence-actions">
          <button
            type="button"
            className="tf-btn sm primary"
            disabled={!cadenceConnected}
            onClick={() => onCadenceAction({ action: "run", selectedNode, projectId: data?.project_id })}
          >
            Run Cadence{selectedNode ? ` on ${selectedNode}` : ""}
          </button>
          <button
            type="button"
            className="tf-btn sm ghost"
            onClick={() => onCadenceAction({ action: "explain", selectedNode })}
          >
            Explain last Cadence run
          </button>
        </footer>
      ) : null}
    </section>
  );
}

function OverviewDetail({ data, selectedNode }) {
  const moduleBlock = selectedModuleBlock(data, selectedNode);
  const scopedPorts = portsForModule(data, selectedNode, moduleBlock);
  const scopedParams = parametersForModule(data, selectedNode, moduleBlock);
  const desc = selectedNode
    ? (
      moduleBlock?.description
      || fallbackModuleDescription(data, selectedNode, scopedPorts)
      || "No module-specific description recorded yet."
    )
    : data.description || (
    data.top_module
      ? `Top module “${data.top_module}” — derived from the project's spec and RTL.`
      : "No description recorded yet."
  );
  const modules = data.modules || [];
  const portCount = selectedNode ? scopedPorts.length : (data.ports || []).length;
  const parameterCount = selectedNode ? scopedParams.length : (data.parameters || []).length;
  return (
    <>
      <h3 className="tf-mm-h3">{selectedNode ? `Module · ${selectedNode}` : "Overview"}</h3>
      <p className="tf-mm-p">{desc}</p>
      <dl className="tf-mm-kv">
        <div><dt>Top module</dt><dd>{data.top_module || "—"}</dd></div>
        <div><dt>Modules</dt><dd>{modules.length}</dd></div>
        <div><dt>Ports</dt><dd>{portCount}</dd></div>
        <div><dt>Parameters</dt><dd>{parameterCount}</dd></div>
        <div><dt>Requirements</dt><dd>{meaningfulRequirements(data.requirements || []).length}</dd></div>
      </dl>
    </>
  );
}

function ModulesDetail({ data, selectedNode }) {
  const tree = data.hierarchy_tree || {};
  const modules = data.modules || [];
  const subInstances = data.sub_instances || [];

  if (selectedNode) {
    const children = tree[selectedNode] || [];
    const parents = Object.entries(tree)
      .filter(([, kids]) => Array.isArray(kids) && kids.includes(selectedNode))
      .map(([k]) => k);
    const instances = subInstances.filter(
      (s) => s.module_name === selectedNode || s.module === selectedNode,
    );

    return (
      <>
        <h3 className="tf-mm-h3">{selectedNode} · relationships</h3>
        <dl className="tf-mm-kv">
          <div>
            <dt>Parents</dt>
            <dd>{parents.length ? parents.join(", ") : "—"}</dd>
          </div>
          <div>
            <dt>Children</dt>
            <dd>{children.length ? children.join(", ") : "—"}</dd>
          </div>
          <div>
            <dt>Instances</dt>
            <dd>{instances.length}</dd>
          </div>
        </dl>
        {instances.length > 0 && (
          <table className="tf-mm-table">
            <thead>
              <tr><th>Instance</th><th>File</th></tr>
            </thead>
            <tbody>
              {instances.map((s, i) => (
                <tr key={i}>
                  <td className="tf-mono">{s.instance_name || s.instance || "—"}</td>
                  <td className="tf-mono">{s.file || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </>
    );
  }

  return (
    <>
      <h3 className="tf-mm-h3">Modules ({modules.length})</h3>
      <div className="tf-mm-chips">
        {modules.length === 0 && <span className="tf-mm-muted">No modules extracted.</span>}
        {modules.map((m) => (
          <span key={m} className="tf-mm-chip">{m}</span>
        ))}
      </div>
      {subInstances.length > 0 && (
        <table className="tf-mm-table">
          <thead>
            <tr><th>Instance</th><th>Module</th><th>File</th></tr>
          </thead>
          <tbody>
            {subInstances.map((s, i) => (
              <tr key={i}>
                <td className="tf-mono">{s.instance_name || s.instance || "—"}</td>
                <td>{s.module_name || s.module || "—"}</td>
                <td className="tf-mono">{s.file || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function portDirectionKey(direction = "") {
  const d = String(direction).toLowerCase();
  if (d.includes("inout")) return "inout";
  if (d.includes("output") || d === "out") return "output";
  if (d.includes("input") || d === "in") return "input";
  return "other";
}

function PortsDetail({ data, selectedNode }) {
  const scopedToTop =
    !selectedNode || selectedNode === data.top_module;
  const moduleBlock = selectedModuleBlock(data, selectedNode);
  const ports = portsForModule(data, selectedNode, moduleBlock);

  const stats = useMemo(() => {
    const counts = { input: 0, output: 0, inout: 0, other: 0 };
    for (const p of ports) {
      counts[portDirectionKey(p.direction)] += 1;
    }
    return counts;
  }, [ports]);

  return (
    <>
      {!scopedToTop && ports.length === 0 && (
        <p className="tf-mm-p tf-mm-muted">
          Port-level data is only recorded for the top module ({data.top_module || "—"}).
          Select the top module or clear scope to see all ports.
        </p>
      )}
      {ports.length > 0 && (
        <>
          <div className="tf-mm-port-stats">
            {stats.input > 0 ? (
              <span className="tf-mm-port-stat input">
                <strong>{stats.input}</strong> input{stats.input === 1 ? "" : "s"}
              </span>
            ) : null}
            {stats.output > 0 ? (
              <span className="tf-mm-port-stat output">
                <strong>{stats.output}</strong> output{stats.output === 1 ? "" : "s"}
              </span>
            ) : null}
            {stats.inout > 0 ? (
              <span className="tf-mm-port-stat inout">
                <strong>{stats.inout}</strong> inout
              </span>
            ) : null}
            <span className="tf-mm-port-stat total">
              <strong>{ports.length}</strong> total on boundary
            </span>
          </div>
          <div className="tf-mm-table-scroll">
            <table className="tf-mm-table">
              <thead>
                <tr>
                  <th>Name</th><th>Dir</th><th>Width</th><th>Description</th>
                </tr>
              </thead>
              <tbody>
                {ports.map((p, i) => (
                  <tr key={p.name || i}>
                    <td className="tf-mono tf-mm-port-name">{p.name}</td>
                    <td>
                      <span className={`tf-mm-dir tf-mm-dir-${portDirectionKey(p.direction)}`}>
                        {p.direction || "—"}
                      </span>
                    </td>
                    <td className="tf-mm-port-width">{p.width ?? 1}</td>
                    <td className="tf-mm-port-desc">{p.description || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {scopedToTop && ports.length === 0 && (
        <span className="tf-mm-muted">No ports recorded.</span>
      )}
    </>
  );
}

function ParametersDetail({ data, selectedNode }) {
  const moduleBlock = selectedModuleBlock(data, selectedNode);
  const params = parametersForModule(data, selectedNode, moduleBlock);
  const clocks = (!selectedNode || selectedNode === data.top_module)
    ? data.clock_domains || []
    : moduleBlock?.clock_domains || [];
  const scopedToTop = !selectedNode || selectedNode === data.top_module;

  return (
    <>
      <h3 className="tf-mm-h3">
        Parameters
        {!scopedToTop && <span className="tf-mm-h3-sub"> · {selectedNode}</span>}
      </h3>
      {!scopedToTop && params.length === 0 && (
        <p className="tf-mm-p tf-mm-muted">
          No parameters are recorded for {selectedNode}. Clear scope to view top-level parameters.
        </p>
      )}
      {scopedToTop && params.length === 0 && (
        <span className="tf-mm-muted">No parameters extracted.</span>
      )}
      {params.length > 0 && (
        <table className="tf-mm-table">
          <thead>
            <tr><th>Name</th><th>Default</th><th>Description</th></tr>
          </thead>
          <tbody>
            {params.map((p, i) => (
              <tr key={i}>
                <td className="tf-mono">{p.name}</td>
                <td className="tf-mono">{p.default_value || "—"}</td>
                <td>{p.description || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {clocks.length > 0 && (
        <>
          <h4 className="tf-mm-h4">Clock domains</h4>
          <ul className="tf-mm-list-plain">
            {clocks.map((c, i) => (
              <li key={i}>
                <strong>{c.name}</strong>
                {c.frequency ? ` · ${c.frequency}` : ""}
                {c.associated_reset ? ` · reset ${c.associated_reset}` : ""}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

function FunctionalitiesDetail({ data, selectedNode }) {
  const moduleBlock = selectedModuleBlock(data, selectedNode);
  const expectedBehaviors = Array.isArray(moduleBlock?.expected_behaviors)
    ? moduleBlock.expected_behaviors
    : [];
  const flows = Array.isArray(moduleBlock?.transaction_flows)
    ? moduleBlock.transaction_flows
    : [];
  const reqs = meaningfulRequirements(data.requirements || []);
  const hasFunctionalItems = expectedBehaviors.length > 0 || flows.length > 0 || reqs.length > 0;
  if (!hasFunctionalItems) {
    return (
      <>
        <h3 className="tf-mm-h3">Functionalities</h3>
        <span className="tf-mm-muted">
          No functional requirements extracted yet.
        </span>
      </>
    );
  }
  return (
    <>
      <h3 className="tf-mm-h3">
        Functionalities ({expectedBehaviors.length + flows.length + reqs.length})
      </h3>
      {expectedBehaviors.length > 0 && (
        <>
          <p className="tf-mm-muted">Module behavior</p>
          <ul className="tf-mm-list-num">
            {expectedBehaviors.map((item, i) => (
              <li key={item.id || item.name || i}>
                {item.name && <code className="tf-mono">{item.name}</code>}
                <span>{behaviorDescriptionOf(item)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {flows.length > 0 && (
        <>
          <p className="tf-mm-muted">Transaction flow</p>
          <ul className="tf-mm-list-num">
            {flows.map((item, i) => (
              <li key={item.id || item.name || i}>
                {item.name && <code className="tf-mono">{item.name}</code>}
                <span>{behaviorDescriptionOf(item)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {reqs.length > 0 && (
        <>
          <p className="tf-mm-muted">Source-grounded requirements</p>
          <ul className="tf-mm-list-num">
            {reqs.map((r, i) => (
              <li key={r.id || i}>
                {r.id && <code className="tf-mono">{r.id}</code>}
                <span>{requirementTextOf(r)}</span>
                {r.priority && (
                  <span className={`tf-mm-pri tf-mm-pri-${r.priority}`}>
                    {r.priority}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
