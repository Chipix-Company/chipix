/**
 * MentalModelViewer — ChipStack-style Design Visualization.
 *
 * Features matching ChipStack's screenshot:
 *  1. Interactive hierarchy graph (SVG node-and-edge diagram)
 *  2. Tabbed interface: Overview | Modules | Functionality | Ports & Interfaces | Design Parameters | Experimental
 *  3. Description section with source references
 *  4. Functionalities list
 *  5. Export buttons
 */

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { queryMentalModel, buildMentalModel } from "../../api/verificationApi";
import {
  Brain, Cpu, FileText, GitBranch, AlertTriangle, ArrowRight,
  CheckCircle2, XCircle, Loader2, ChevronDown, ChevronRight,
  Layers, Zap, Shield, HelpCircle, RefreshCw, Download,
  Network, Settings, Beaker, BarChart3, Clock, Activity
} from "lucide-react";
import "./mental_model_viewer.css";

const TABS = [
  { id: "overview", label: "Overview", icon: Brain },
  { id: "modules", label: "Modules", icon: Cpu },
  { id: "functionality", label: "Functionality", icon: FileText },
  { id: "ports", label: "Ports & Interfaces", icon: Network },
  { id: "parameters", label: "Design Parameters", icon: Settings },
  { id: "experimental", label: "Experimental", icon: Beaker },
];

function normalizeMentalModelResponse(raw) {
  if (!raw) return null;

  const revision =
    raw.mental_model && typeof raw.mental_model === "object"
      ? raw.mental_model
      : raw;
  const content =
    revision.content && typeof revision.content === "object"
      ? revision.content
      : raw.content && typeof raw.content === "object"
        ? raw.content
        : revision;
  const model =
    content.content && typeof content.content === "object"
      ? content.content
      : content;
  const metadata = {
    id: revision.id || raw.id,
    revision: revision.revision || raw.revision,
    status: revision.status || raw.status,
    summary_text: revision.summary_text || raw.summary_text,
    schema_version: revision.schema_version || raw.schema_version,
    created_at: revision.created_at || raw.created_at,
  };

  // Backend now returns a flat shape directly — but handle both old (nested) and new (flat).
  const design = (model.design && typeof model.design === "object") ? model.design : {};
  const projectScan = model.project_scan || design.project_scan || {};

  return {
    // Spread design fields first so top_module, ports, etc. are always available
    ...design,
    // Then spread raw to let top-level fields (revision, id, requirements) win
    ...model,
    ...metadata,
    // Ensure these are always normalized arrays/objects
    design,
    requirements: model.requirements || design.requirements || [],
    verification: model.verification || design.verification || {},
    evidence: model.evidence || design.evidence || [],
    open_questions: model.open_questions || design.open_questions || [],
    source_refs: model.source_refs || design.source_refs || [],
    source_files:
      model.source_files ||
      design.source_files ||
      projectScan.rtl_files ||
      [],
    // Key display fields with safe fallbacks
    top_module: model.top_module || design.top_module || "",
    modules: model.modules || design.modules || [],
    ports: model.ports || design.ports || [],
    parameters: model.parameters || design.parameters || [],
    clock_domains: model.clock_domains || design.clock_domains || [],
    fsms: model.fsms || design.fsms || [],
    protocols: model.protocols || design.protocols || [],
    sub_instances: model.sub_instances || design.sub_instances || [],
    hierarchy_tree: model.hierarchy_tree || design.hierarchy_tree || {},
    arbitration_policies: model.arbitration_policies || design.arbitration_policies || [],
    transaction_flows: model.transaction_flows || design.transaction_flows || [],
    description: model.description || design.description || "",
  };
}

export default function MentalModelViewer({ projectId, authToken, onModelLoaded, hideBuildButton = false }) {
  const [activeTab, setActiveTab] = useState("overview");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState(null);

  const loadData = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const result = await queryMentalModel(projectId, "overview", authToken);
      const normalized = normalizeMentalModelResponse(result);
      setData(normalized);
      if (onModelLoaded) onModelLoaded(normalized);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [projectId, authToken, onModelLoaded]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleBuild = async () => {
    setBuilding(true);
    setError(null);
    try {
      await buildMentalModel(projectId, {}, authToken);
      await loadData(activeTab);
    } catch (e) {
      setError(e.message);
    } finally {
      setBuilding(false);
    }
  };

  return (
    <div className="mm-viewer">
      {/* Header Bar */}
      <div className="mm-header">
        <div className="mm-header-left">
          <Brain size={18} className="mm-header-icon" />
          <span className="mm-header-title">Mental Model</span>
          {data?.top_module && (
            <span className="mm-header-file">
              <FileText size={12} />
              {data.top_module}.sv
            </span>
          )}
        </div>
        <div className="mm-header-actions">
          <button className="mm-btn mm-btn-export" onClick={() => {}}>
            <Download size={13} /> Export
          </button>
          <button className="mm-btn mm-btn-export-arch" onClick={() => {}}>
            <Download size={13} /> Export Microarchitecture
          </button>
          {!hideBuildButton && (
            <button
              className="mm-btn mm-btn-build"
              onClick={handleBuild}
              disabled={building}
            >
              {building ? <Loader2 size={14} className="spin" /> : <Zap size={14} />}
              {building ? "Building..." : "Build Model"}
            </button>
          )}
        </div>
      </div>

      {/* Tab Bar */}
      <div className="mm-tabs">
        {TABS.map(tab => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              className={`mm-tab ${activeTab === tab.id ? "mm-tab-active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <Icon size={13} />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>

      {/* Content */}
      <div className="mm-content">
        {error && (
          <div className="mm-error">
            <AlertTriangle size={14} />
            <span>{error}</span>
          </div>
        )}

        {loading && !data && (
          <div className="mm-loading">
            <Loader2 size={20} className="spin" />
            <span>Loading mental model...</span>
          </div>
        )}

        {data && !error && (
          <>
            {activeTab === "overview" && <OverviewTab data={data} />}
            {activeTab === "modules" && <ModulesTab data={data} />}
            {activeTab === "functionality" && <FunctionalityTab data={data} />}
            {activeTab === "ports" && <PortsTab data={data} />}
            {activeTab === "parameters" && <ParametersTab data={data} />}
            {activeTab === "experimental" && <ExperimentalTab data={data} />}
          </>
        )}

        {!data && !loading && !error && (
          <div className="mm-empty">
            <Brain size={48} className="mm-empty-icon" />
            <p className="mm-empty-title">No mental model yet</p>
            <p className="mm-empty-hint">Click "Build Model" to analyze the design.</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Overview Tab — Hierarchy Graph + Description + Functionalities
// ═══════════════════════════════════════════════════════════════════════

function OverviewTab({ data }) {
  return (
    <div className="mm-overview">
      {/* Hierarchy Graph */}
      <HierarchyGraph data={data} />

      {/* Description */}
      <div className="mm-description-section">
        <h3 className="mm-section-title">DESCRIPTION</h3>
        <p className="mm-description-text">
          {data.description || `The "${data.top_module}" module implements the design as analyzed from RTL source.`}
          {data.source_refs && data.source_refs.length > 0 && (
            <span className="mm-refs">
              {" "}
              {data.source_refs.map((ref, i) => (
                <a key={i} className="mm-ref-link" href="#" title={ref}>[{i}]</a>
              ))}
            </span>
          )}
        </p>
      </div>

      {/* Functionalities */}
      {data.requirements && data.requirements.length > 0 && (
        <div className="mm-functionality-section">
          <h3 className="mm-section-title">FUNCTIONALITIES</h3>
          <ul className="mm-func-list">
            {data.requirements.slice(0, 5).map((r, i) => (
              <li key={i}>{r.text || r}</li>
            ))}
          </ul>
          {data.requirements.length > 5 && (
            <button className="mm-btn-more">+{data.requirements.length - 5} more →</button>
          )}
        </div>
      )}

      {/* References */}
      {data.source_files && data.source_files.length > 0 && (
        <div className="mm-references-section">
          <h3 className="mm-section-title">~ REFERENCES</h3>
          <ul className="mm-ref-list">
            {data.source_files.map((file, i) => (
              <li key={i}>
                <a className="mm-ref-file" href="#">[{i}] {file}</a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Hierarchy Graph — Interactive SVG node-and-edge diagram
// ═══════════════════════════════════════════════════════════════════════

function HierarchyGraph({ data }) {
  const svgRef = useRef(null);
  const [hoveredNode, setHoveredNode] = useState(null);

  const { nodes, edges, width, height } = useMemo(() => {
    return layoutHierarchy(data);
  }, [data]);

  if (nodes.length === 0) {
    return (
      <div className="mm-graph-empty">
        <Network size={32} />
        <p>No hierarchy data available</p>
      </div>
    );
  }

  return (
    <div className="mm-graph-container">
      <svg
        ref={svgRef}
        className="mm-graph-svg"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
      >
        {/* Edges */}
        {edges.map((edge, i) => (
          <line
            key={`e-${i}`}
            x1={edge.x1} y1={edge.y1}
            x2={edge.x2} y2={edge.y2}
            className="mm-graph-edge"
          />
        ))}

        {/* Nodes */}
        {nodes.map((node, i) => (
          <g
            key={`n-${i}`}
            transform={`translate(${node.x}, ${node.y})`}
            className={`mm-graph-node ${hoveredNode === node.name ? "mm-graph-node-hover" : ""}`}
            onMouseEnter={() => setHoveredNode(node.name)}
            onMouseLeave={() => setHoveredNode(null)}
          >
            <rect
              x={-node.width / 2}
              y={-16}
              width={node.width}
              height={32}
              rx={6}
              className="mm-graph-node-rect"
            />
            <text
              textAnchor="middle"
              dominantBaseline="central"
              className="mm-graph-node-text"
            >
              {node.name}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function layoutHierarchy(data) {
  const nodes = [];
  const edges = [];
  const tree = data.hierarchy_tree || {};
  const allModules = data.modules || [];
  const topModule = data.top_module || "";

  if (allModules.length === 0 && !topModule) {
    return { nodes: [], edges: [], width: 100, height: 100 };
  }

  // Build levels using BFS
  const levels = [];
  const visited = new Set();
  const nodePositions = {};

  // Level 0: top module
  const queue = [topModule || allModules[0]];
  visited.add(queue[0]);

  while (queue.length > 0) {
    const levelNodes = [...queue];
    levels.push(levelNodes);
    queue.length = 0;
    for (const node of levelNodes) {
      const children = tree[node] || [];
      for (const child of children) {
        if (!visited.has(child)) {
          visited.add(child);
          queue.push(child);
        }
      }
    }
  }

  // Add unvisited modules as a final level
  const unvisited = allModules.filter(m => !visited.has(m));
  if (unvisited.length > 0) {
    levels.push(unvisited);
  }

  // Layout
  const NODE_H = 32;
  const NODE_PAD = 20;
  const LEVEL_GAP = 70;
  const CHAR_WIDTH = 9;
  const MIN_NODE_W = 80;

  let maxLevelWidth = 0;

  levels.forEach((level, levelIdx) => {
    const y = 40 + levelIdx * LEVEL_GAP;
    const totalWidth = level.reduce((sum, name) => {
      return sum + Math.max(MIN_NODE_W, name.length * CHAR_WIDTH + 24) + NODE_PAD;
    }, -NODE_PAD);

    maxLevelWidth = Math.max(maxLevelWidth, totalWidth);
    let x = 0;

    level.forEach(name => {
      const w = Math.max(MIN_NODE_W, name.length * CHAR_WIDTH + 24);
      const cx = x + w / 2;
      nodes.push({ name, x: cx, y, width: w, level: levelIdx });
      nodePositions[name] = { x: cx, y };
      x += w + NODE_PAD;
    });

    // Center the level
    const offset = (maxLevelWidth - totalWidth) / 2;
    level.forEach(name => {
      nodePositions[name].x += offset + 30;
      const node = nodes.find(n => n.name === name);
      if (node) node.x += offset + 30;
    });
  });

  // Create edges
  for (const [parent, children] of Object.entries(tree)) {
    const parentPos = nodePositions[parent];
    if (!parentPos) continue;
    for (const child of children) {
      const childPos = nodePositions[child];
      if (!childPos) continue;
      edges.push({
        x1: parentPos.x, y1: parentPos.y + 16,
        x2: childPos.x, y2: childPos.y - 16,
      });
    }
  }

  return {
    nodes,
    edges,
    width: maxLevelWidth + 60,
    height: levels.length * LEVEL_GAP + 40,
  };
}

// ═══════════════════════════════════════════════════════════════════════
// Modules Tab
// ═══════════════════════════════════════════════════════════════════════

function ModulesTab({ data }) {
  const modules = data.modules || [];
  const subInstances = data.sub_instances || [];

  return (
    <div className="mm-section">
      <div className="mm-stat-grid">
        <StatCard label="Total Modules" value={modules.length} icon={Cpu} />
        <StatCard label="Instances" value={subInstances.length} icon={Layers} />
        <StatCard label="Lines" value={data.total_lines || "—"} icon={FileText} />
        <StatCard label="Always Blocks" value={data.total_always_blocks || "—"} icon={Activity} />
      </div>

      {modules.length > 0 && (
        <>
          <h4 className="mm-sub-title">Module List</h4>
          <div className="mm-chip-list">
            {modules.map(m => (
              <span key={m} className="mm-chip">{m}</span>
            ))}
          </div>
        </>
      )}

      {subInstances.length > 0 && (
        <>
          <h4 className="mm-sub-title">Instance Table</h4>
          <table className="mm-table">
            <thead>
              <tr><th>Instance Name</th><th>Module</th><th>File</th></tr>
            </thead>
            <tbody>
              {subInstances.map((s, i) => (
                <tr key={i}>
                  <td className="mm-mono">{s.instance_name || s.instance}</td>
                  <td>{s.module_name || s.module}</td>
                  <td className="mm-mono">{s.file || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Functionality Tab
// ═══════════════════════════════════════════════════════════════════════

function FunctionalityTab({ data }) {
  const reqs = data.requirements || [];
  if (reqs.length === 0) return <EmptyState text="No functionality data extracted" />;

  return (
    <div className="mm-section">
      <table className="mm-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Requirement</th>
            <th>Priority</th>
            <th>Category</th>
            <th>Verified</th>
          </tr>
        </thead>
        <tbody>
          {reqs.map((r, i) => (
            <tr key={i}>
              <td className="mm-mono">{r.id}</td>
              <td className="mm-req-text">{r.text}</td>
              <td><PriorityBadge priority={r.priority} /></td>
              <td>{r.category || "—"}</td>
              <td>
                {r.verified_by && r.verified_by.length > 0
                  ? <CheckCircle2 size={14} className="mm-icon-pass" />
                  : <XCircle size={14} className="mm-icon-fail" />}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Ports & Interfaces Tab
// ═══════════════════════════════════════════════════════════════════════

function PortsTab({ data }) {
  const ports = data.ports || [];
  const protocols = data.protocols || [];
  const flows = data.transaction_flows || [];

  return (
    <div className="mm-section">
      {/* Port Table */}
      <div className="mm-port-header">
        <span>Module: <strong>{data.top_module}</strong></span>
        <span className="mm-port-count">{ports.length} ports</span>
      </div>

      {ports.length > 0 && (
        <table className="mm-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Direction</th>
              <th>Width</th>
              <th>Range</th>
              <th>Protocol Role</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {ports.map((p, i) => (
              <tr key={i}>
                <td className="mm-port-name">{p.name}</td>
                <td><span className={`mm-dir mm-dir-${p.direction}`}>{p.direction}</span></td>
                <td>{p.width || 1}</td>
                <td className="mm-mono">{p.bus_range || "—"}</td>
                <td className="mm-mono">{p.protocol_role || "—"}</td>
                <td className="mm-desc">{p.description || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Protocols */}
      {protocols.length > 0 && (
        <>
          <h4 className="mm-sub-title">Interface Protocols</h4>
          <div className="mm-protocol-grid">
            {protocols.map((p, i) => (
              <div key={i} className="mm-protocol-card">
                <div className="mm-protocol-name">{p.protocol}</div>
                <div className="mm-protocol-role">{p.role || "N/A"}</div>
                {p.port_group && p.port_group.length > 0 && (
                  <div className="mm-protocol-ports">
                    {p.port_group.map(port => (
                      <span key={port} className="mm-chip mm-chip-small">{port}</span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {/* Transaction Flows */}
      {flows.length > 0 && (
        <>
          <h4 className="mm-sub-title">Transaction Flows</h4>
          {flows.map((flow, i) => (
            <div key={i} className="mm-flow-card">
              <div className="mm-flow-header">
                <span className="mm-flow-name">{flow.name}</span>
                {flow.protocol && <span className="mm-chip mm-chip-small">{flow.protocol}</span>}
                {flow.latency_cycles > 0 && (
                  <span className="mm-flow-latency">
                    <Clock size={11} /> {flow.latency_cycles} cycles
                  </span>
                )}
              </div>
              {flow.steps && flow.steps.length > 0 && (
                <div className="mm-flow-steps">
                  {flow.steps.map((step, j) => (
                    <span key={j} className="mm-flow-step">
                      {j > 0 && <ArrowRight size={10} className="mm-flow-arrow" />}
                      {step.phase}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Design Parameters Tab
// ═══════════════════════════════════════════════════════════════════════

function ParametersTab({ data }) {
  const params = data.parameters || [];
  const clocks = data.clock_domains || [];
  const fsms = data.fsms || [];

  return (
    <div className="mm-section">
      {/* Parameters */}
      {params.length > 0 && (
        <>
          <h4 className="mm-sub-title">Parameters</h4>
          <table className="mm-table">
            <thead>
              <tr><th>Name</th><th>Default</th><th>Description</th></tr>
            </thead>
            <tbody>
              {params.map((p, i) => (
                <tr key={i}>
                  <td className="mm-mono">{p.name}</td>
                  <td className="mm-mono">{p.default_value || "—"}</td>
                  <td>{p.description || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {/* Clock Domains */}
      {clocks.length > 0 && (
        <>
          <h4 className="mm-sub-title">Clock Domains</h4>
          <div className="mm-clock-grid">
            {clocks.map((c, i) => (
              <div key={i} className="mm-clock-card">
                <div className="mm-clock-name">
                  <Clock size={13} /> {c.name}
                </div>
                <div className="mm-clock-details">
                  {c.frequency && <span>Freq: {c.frequency}</span>}
                  {c.associated_reset && <span>Reset: {c.associated_reset}</span>}
                  {c.reset_polarity && <span>{c.reset_polarity}</span>}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* FSMs */}
      {fsms.length > 0 && (
        <>
          <h4 className="mm-sub-title">Finite State Machines</h4>
          {fsms.map((fsm, i) => (
            <div key={i} className="mm-fsm-card">
              <div className="mm-fsm-name">{fsm.name}</div>
              <div className="mm-fsm-states">
                {(fsm.states || []).map(s => (
                  <span key={s} className="mm-chip mm-chip-state">{s}</span>
                ))}
              </div>
              {fsm.transitions && fsm.transitions.length > 0 && (
                <div className="mm-fsm-transitions">
                  {fsm.transitions.map((t, j) => (
                    <span key={j} className="mm-fsm-trans">
                      {t.from} <ArrowRight size={10} /> {t.to}
                      {t.condition && <span className="mm-fsm-cond">({t.condition})</span>}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Experimental Tab (Arbitration, Coverage, Evidence)
// ═══════════════════════════════════════════════════════════════════════

function ExperimentalTab({ data }) {
  const arb = data.arbitration_policies || [];
  const evidence = data.evidence || [];
  const questions = data.open_questions || [];

  return (
    <div className="mm-section">
      {/* Arbitration Policies */}
      {arb.length > 0 && (
        <>
          <h4 className="mm-sub-title">Arbitration & QoS</h4>
          {arb.map((a, i) => (
            <div key={i} className="mm-arb-card">
              <div className="mm-arb-header">
                <span className="mm-arb-name">{a.name}</span>
                <span className="mm-chip mm-chip-small">{a.scheme}</span>
                {a.qos_support && <span className="mm-chip mm-chip-qos">QoS</span>}
              </div>
              <div className="mm-arb-masters">
                Masters: {(a.masters || []).map(m => (
                  <span key={m} className="mm-chip mm-chip-small">{m}</span>
                ))}
              </div>
              {a.bandwidth_allocation && Object.keys(a.bandwidth_allocation).length > 0 && (
                <div className="mm-arb-bw">
                  {Object.entries(a.bandwidth_allocation).map(([k, v]) => (
                    <span key={k} className="mm-arb-bw-item">{k}: {v}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </>
      )}

      {/* Open Questions */}
      {questions.length > 0 && (
        <>
          <h4 className="mm-sub-title">Open Questions</h4>
          {questions.map((q, i) => (
            <div key={i} className={`mm-question ${q.blocking ? "mm-question-blocking" : ""}`}>
              <div className="mm-question-header">
                <HelpCircle size={14} />
                <span className="mm-question-id">{q.id}</span>
                {q.blocking && <span className="mm-badge-blocking">BLOCKING</span>}
              </div>
              <p className="mm-question-text">{q.question}</p>
              {q.answered && (
                <p className="mm-question-answer">
                  <CheckCircle2 size={12} /> {q.answer || "Answered"}
                </p>
              )}
            </div>
          ))}
        </>
      )}

      {/* Evidence from runs */}
      {evidence.length > 0 && (
        <>
          <h4 className="mm-sub-title">Verification Evidence</h4>
          <table className="mm-table">
            <thead>
              <tr><th>Agent</th><th>Type</th><th>Summary</th></tr>
            </thead>
            <tbody>
              {evidence.map((e, i) => (
                <tr key={i}>
                  <td className="mm-mono">{e.agent}</td>
                  <td>{e.evidence_type}</td>
                  <td>{e.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {arb.length === 0 && questions.length === 0 && evidence.length === 0 && (
        <EmptyState text="No experimental data yet — build the mental model and run verification" />
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Shared Components
// ═══════════════════════════════════════════════════════════════════════

function StatCard({ label, value, icon: Icon }) {
  return (
    <div className="mm-stat-card">
      <Icon size={16} className="mm-stat-icon" />
      <div className="mm-stat-value">{value}</div>
      <div className="mm-stat-label">{label}</div>
    </div>
  );
}

function PriorityBadge({ priority }) {
  const colors = {
    critical: "#ef4444",
    high: "#f59e0b",
    medium: "#3b82f6",
    low: "#6b7280",
  };
  return (
    <span
      className="mm-priority-badge"
      style={{ backgroundColor: `${colors[priority] || "#6b7280"}20`, color: colors[priority] }}
    >
      {priority}
    </span>
  );
}

function EmptyState({ text }) {
  return (
    <div className="mm-empty">
      <p>{text}</p>
    </div>
  );
}
