import React, { startTransition, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Icon from "../icons";
import {
  createSimulation,
  getSimulationStatus,
  getToolchainStatus,
  getWaveformSlice,
  renderNetlist,
  streamSimulationEvents,
} from "../../../services/edaWorkspaceApi";
import {
  findNodeForEditorSelection,
  parseRtlSource,
} from "../../../services/rtlParserClient";

/**
 * RtlVisualizer — thread-first port of the legacy RTLVisualizer.
 *
 * First-principles redesign: tabs become **three stacked sections** because
 * the user, looking at a Verilog file, almost always wants to see hierarchy
 * AND the schematic AND any captured waveform together — switching tabs
 * is friction. Sections are individually collapsible to keep the rail
 * compact when one of them isn't ready (e.g. schematic not rendered yet).
 *
 * **Schematic (Yosys netlist view):** gives a gate-level sanity check —
 * engineers use it to confirm elaboration matches intent (ports, hierarchy,
 * obvious structural mistakes) before spending time in sim or formal.
 *
 * Lives inside the IDE's right column, above the assistant, only when the
 * current file looks like RTL (.sv/.svh/.v/.vh).
 */

function CapBadge({ label, ok }) {
  return (
    <span className={`tf-rtl-vis-cap ${ok ? "ok" : "miss"}`}>
      <span className="d" />
      {label}
    </span>
  );
}

function renderWavePath(transitions, width, height, t0, t1) {
  if (!transitions?.length || t0 === t1) return "";
  const norm = (t) => ((t - t0) / Math.max(1, t1 - t0)) * width;
  const hi = 6;
  const lo = height - 6;
  let prev = transitions[0]?.value === "1" ? hi : lo;
  let path = `M 0 ${prev}`;
  transitions.forEach((tr) => {
    const x = norm(Number(tr.time || 0));
    const next = String(tr.value) === "1" ? hi : lo;
    path += ` L ${x} ${prev} L ${x} ${next}`;
    prev = next;
  });
  path += ` L ${width} ${prev}`;
  return path;
}

function HierarchyNode({ node, selectedId, onSelect, depth = 0 }) {
  const active = node.id === selectedId;
  return (
    <div className="tf-rtl-vis-node-group">
      <button
        type="button"
        className={`tf-rtl-vis-node ${active ? "active" : ""}`}
        style={{ paddingLeft: 6 + depth * 12 }}
        onClick={() => onSelect(node)}
      >
        <span className="lbl">{node.label}</span>
        {node.targetModuleName ? <span className="meta">{node.targetModuleName}</span> : null}
        {node.direction ? <span className="meta">{node.direction}</span> : null}
      </button>
      {node.children?.length ? (
        <div className="tf-rtl-vis-node-children">
          {node.children.map((c) => (
            <HierarchyNode key={c.id} node={c} selectedId={selectedId} onSelect={onSelect} depth={depth + 1} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default function RtlVisualizer({
  rtlCode = "",
  artifactLabel = "",
  projectId = "",
  authToken = "",
  editorSelection = null,
  onSelectRange,
}) {
  const [snapshot, setSnapshot] = useState(null);
  const [parseError, setParseError] = useState("");
  const [parseLoading, setParseLoading] = useState(false);
  const [toolchain, setToolchain] = useState(null);
  const [schematic, setSchematic] = useState(null);
  const [schematicLoading, setSchematicLoading] = useState(false);
  const [schematicError, setSchematicError] = useState("");
  const [simulation, setSimulation] = useState(null);
  const [simStarting, setSimStarting] = useState(false);
  const [simError, setSimError] = useState("");
  const [simEvents, setSimEvents] = useState([]);
  const [waveform, setWaveform] = useState(null);
  const [selectedNode, setSelectedNode] = useState(null);
  const [selectedSignal, setSelectedSignal] = useState("");
  const [open, setOpen] = useState({ hier: true, schem: true, wave: true });
  const [schematicLightbox, setSchematicLightbox] = useState(false);
  const [schematicZoom, setSchematicZoom] = useState(1);

  const streamStopRef = useRef(null);
  const simRunRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    setParseLoading(true);
    setParseError("");
    (async () => {
      try {
        const snap = await parseRtlSource(rtlCode || "");
        if (cancelled) return;
        startTransition(() => setSnapshot(snap));
      } catch (err) {
        if (!cancelled) {
          setParseError(err?.message || "Unable to parse buffer.");
          setSnapshot(null);
        }
      } finally {
        if (!cancelled) setParseLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [rtlCode]);

  useEffect(() => {
    let disposed = false;
    getToolchainStatus(authToken)
      .then((p) => { if (!disposed) setToolchain(p?.tools || {}); })
      .catch(() => { if (!disposed) setToolchain({}); });
    return () => { disposed = true; };
  }, [authToken]);

  useEffect(() => () => {
    if (streamStopRef.current) streamStopRef.current();
  }, []);

  const openSchematicViewer = useCallback(() => {
    setSchematicZoom(1);
    setSchematicLightbox(true);
  }, []);

  const closeSchematicViewer = useCallback(() => {
    setSchematicLightbox(false);
  }, []);

  useEffect(() => {
    if (!schematicLightbox) return;
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeSchematicViewer();
      }
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [schematicLightbox, closeSchematicViewer]);

  useEffect(() => {
    if (!snapshot) {
      setSelectedNode(null);
      return;
    }
    const match = findNodeForEditorSelection(snapshot, editorSelection);
    if (match) {
      setSelectedNode(match);
      if (match.name) setSelectedSignal(match.name);
    }
  }, [editorSelection, snapshot]);

  const hierarchy = snapshot?.hierarchy || [];
  const topModuleName = snapshot?.topModuleName || "";

  const signalCandidates = useMemo(() => {
    if (selectedSignal) return [selectedSignal];
    return snapshot?.modules?.[0]?.signals?.slice(0, 4).map((s) => s.name) || [];
  }, [selectedSignal, snapshot]);

  const handleNodeSelect = (node) => {
    setSelectedNode(node);
    if (node.name || node.signalName) setSelectedSignal(node.name || node.signalName);
    if (node.range && onSelectRange) onSelectRange(node.range);
  };

  const handleRenderSchematic = async () => {
    if (!projectId || !rtlCode?.trim()) return;
    setSchematicLoading(true);
    setSchematicError("");
    try {
      const next = await renderNetlist({
        projectId,
        filename: artifactLabel || "workspace.sv",
        source: rtlCode,
        topModule: topModuleName || undefined,
      }, authToken);
      setSchematic(next);
    } catch (err) {
      setSchematicError(err?.message || "Unable to render schematic.");
    } finally {
      setSchematicLoading(false);
    }
  };

  const handleStartSimulation = async () => {
    if (!projectId || !rtlCode?.trim() || !topModuleName || simStarting) return;
    const runId = simRunRef.current + 1;
    simRunRef.current = runId;
    if (streamStopRef.current) {
      streamStopRef.current();
      streamStopRef.current = null;
    }
    setSimStarting(true);
    setSimError("");
    setSimEvents([]);
    setWaveform(null);
    try {
      const created = await createSimulation({
        projectId,
        filename: artifactLabel || "workspace.sv",
        rtlSource: rtlCode,
        topModule: topModuleName,
      }, authToken);
      if (simRunRef.current !== runId) return;
      setSimulation(created);
      const stop = streamSimulationEvents(created.simulation_id, {
        onEvent: (payload) => {
          if (simRunRef.current !== runId) return;
          if (payload?.event) setSimEvents((prev) => [...prev, payload.event].slice(-20));
        },
        onDone: async () => {
          if (simRunRef.current !== runId) return;
          try {
            const status = await getSimulationStatus(created.simulation_id, authToken);
            if (simRunRef.current !== runId) return;
            setSimulation(status);
            if (status.status === "completed") {
              const wave = await getWaveformSlice(created.simulation_id, { signals: signalCandidates }, authToken);
              if (simRunRef.current === runId) setWaveform(wave);
            }
          } catch (err) {
            if (simRunRef.current === runId) setSimError(err?.message || "Simulation status sync failed.");
          } finally {
            if (simRunRef.current === runId) setSimStarting(false);
          }
        },
        onError: (err) => {
          if (simRunRef.current === runId) {
            setSimError(err?.message || "Simulation event stream failed.");
            setSimStarting(false);
          }
        },
      }, authToken);
      streamStopRef.current = stop;
    } catch (err) {
      setSimError(err?.message || "Unable to start simulation.");
      setSimStarting(false);
    }
  };

  const toggle = (k) => setOpen((s) => ({ ...s, [k]: !s[k] }));

  const schematicPortal =
    typeof document !== "undefined" && schematicLightbox && schematic?.schematic_svg
      ? createPortal(
        (
          <div
            className="tf-rtl-sch-lightbox"
            role="dialog"
            aria-modal="true"
            aria-labelledby="tf-rtl-sch-lb-title"
          >
            <button
              type="button"
              className="tf-rtl-sch-lightbox-bg"
              aria-label="Close schematic viewer"
              onClick={closeSchematicViewer}
            />
            <div className="tf-rtl-sch-lightbox-card">
              <header className="tf-rtl-sch-lightbox-h">
                <span className="t" id="tf-rtl-sch-lb-title">Schematic</span>
                <span className="s">{artifactLabel || "buffer"}</span>
                <span className="sp" />
                <div className="zoom-ctl">
                  <button
                    type="button"
                    className="tf-btn sm"
                    onClick={() => setSchematicZoom((z) => Math.round(Math.max(0.35, z - 0.15) * 100) / 100)}
                    aria-label="Zoom out"
                  >
                    −
                  </button>
                  <span>{Math.round(schematicZoom * 100)}%</span>
                  <button
                    type="button"
                    className="tf-btn sm"
                    onClick={() => setSchematicZoom((z) => Math.round(Math.min(2.75, z + 0.15) * 100) / 100)}
                    aria-label="Zoom in"
                  >
                    +
                  </button>
                  <button type="button" className="tf-btn sm" onClick={() => setSchematicZoom(1)}>100%</button>
                </div>
                <button
                  type="button"
                  className="tf-rtl-sch-lightbox-close"
                  onClick={closeSchematicViewer}
                  aria-label="Close"
                >
                  <Icon.Close width="16" height="16" />
                </button>
              </header>
              <div className="tf-rtl-sch-lightbox-body">
                <div className="tf-rtl-sch-lightbox-zoom" style={{ zoom: schematicZoom }}>
                  <div dangerouslySetInnerHTML={{ __html: schematic.schematic_svg }} />
                </div>
              </div>
            </div>
          </div>
        ),
        document.body,
      )
      : null;

  return (
    <>
      <div className="tf-rtl-vis">
      <div className="tf-rtl-vis-toolbar">
        <Icon.Cpu width="12" height="12" />
        <span className="title">RTL view</span>
        <span className="sub">{artifactLabel || "buffer"}</span>
      </div>
      <div className="tf-rtl-vis-caps">
        <CapBadge label="Parser" ok={Boolean(snapshot)} />
        <CapBadge label="Yosys" ok={Boolean(toolchain?.yosys?.available)} />
        <CapBadge label="Verilator" ok={Boolean(toolchain?.verilator?.available)} />
      </div>
      {parseError ? <div className="tf-rtl-vis-err">{parseError}</div> : null}

      {/* ── Hierarchy ─────────────────────────────────────────────── */}
      <section className={`tf-rtl-vis-sec ${open.hier ? "open" : ""}`}>
        <button className="hdr" onClick={() => toggle("hier")}>
          <span className="caret">
            {open.hier ? <Icon.ChevronDown width="10" height="10" /> : <Icon.ChevronRight width="10" height="10" />}
          </span>
          <Icon.Layers width="12" height="12" />
          <span className="label">Hierarchy</span>
          <span className="meta">{hierarchy.length ? `${hierarchy.length} module${hierarchy.length === 1 ? "" : "s"}` : ""}</span>
        </button>
        {open.hier ? (
          <div className="body">
            {parseLoading ? (
              <div className="empty">Parsing buffer…</div>
            ) : hierarchy.length === 0 ? (
              <div className="empty">No SystemVerilog modules detected.</div>
            ) : (
              <div className="tree">
                {hierarchy.map((n) => (
                  <HierarchyNode key={n.id} node={n} selectedId={selectedNode?.id} onSelect={handleNodeSelect} />
                ))}
              </div>
            )}
          </div>
        ) : null}
      </section>

      {/* ── Schematic ─────────────────────────────────────────────── */}
      <section className={`tf-rtl-vis-sec ${open.schem ? "open" : ""}`}>
        <button className="hdr" onClick={() => toggle("schem")}>
          <span className="caret">
            {open.schem ? <Icon.ChevronDown width="10" height="10" /> : <Icon.ChevronRight width="10" height="10" />}
          </span>
          <Icon.Cpu width="12" height="12" />
          <span className="label">Schematic</span>
          {schematic ? <span className="meta">rendered</span> : null}
        </button>
        {open.schem ? (
          <div className="body">
            <div className="row">
              <button
                className="tf-btn sm"
                onClick={handleRenderSchematic}
                disabled={schematicLoading || !projectId}
                title={!projectId ? "Open a project first" : "Render via yosys"}
              >
                {schematicLoading ? "Rendering…" : schematic ? "Re-render" : "Render schematic"}
              </button>
            </div>
            {schematicError ? <div className="err">{schematicError}</div> : null}
            {schematic?.schematic_svg ? (
              <>
                <div className="tf-rtl-vis-canvas-row">
                  <button type="button" className="tf-btn sm" onClick={openSchematicViewer}>
                    View full size
                  </button>
                  <span className="tf-rtl-vis-canvas-hint">Click preview · scroll in viewer · − / + zoom</span>
                </div>
                <div
                  role="button"
                  tabIndex={0}
                  className="canvas tf-rtl-vis-canvas--clickable"
                  onClick={openSchematicViewer}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      openSchematicViewer();
                    }
                  }}
                  dangerouslySetInnerHTML={{ __html: schematic.schematic_svg }}
                  aria-label="Open schematic full size"
                />
              </>
            ) : (
              <div className="empty">Render to see gate-level structure from the buffer.</div>
            )}
          </div>
        ) : null}
      </section>

      {/* ── Waveform ──────────────────────────────────────────────── */}
      <section className={`tf-rtl-vis-sec ${open.wave ? "open" : ""}`}>
        <button className="hdr" onClick={() => toggle("wave")}>
          <span className="caret">
            {open.wave ? <Icon.ChevronDown width="10" height="10" /> : <Icon.ChevronRight width="10" height="10" />}
          </span>
          <Icon.Wave width="12" height="12" />
          <span className="label">Waveform</span>
          {simulation?.status ? <span className="meta">{simulation.status}</span> : null}
        </button>
        {open.wave ? (
          <div className="body">
            <div className="row">
              <button
                className="tf-btn sm"
                onClick={handleStartSimulation}
                disabled={!projectId || !topModuleName || simStarting}
                title={!topModuleName ? "Top module needed" : !projectId ? "Open a project first" : "Run via verilator"}
              >
                <Icon.Play width="11" height="11" />
                {simStarting ? "Starting…" : "Run simulation"}
              </button>
            </div>
            {simError ? <div className="err">{simError}</div> : null}
            {waveform?.signals?.length ? (
              <div className="wave">
                <div className="wave-meta">
                  <span>{waveform.timescale}</span>
                  <span>{waveform.start_time} – {waveform.end_time}</span>
                </div>
                {waveform.signals.map((sig) => (
                  <div
                    key={sig.name}
                    className={`wave-row ${selectedSignal === sig.name ? "picked" : ""}`}
                    onClick={() => setSelectedSignal(sig.name)}
                  >
                    <span className="sig">{sig.name}</span>
                    <svg viewBox="0 0 260 22" preserveAspectRatio="none">
                      <path
                        d={renderWavePath(sig.transitions, 260, 22, waveform.start_time, waveform.end_time)}
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.5"
                      />
                    </svg>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty">No waveform yet. Run a simulation to populate.</div>
            )}
            {simEvents.length ? (
              <div className="evlog">
                {simEvents.slice(-6).map((ev) => (
                  <div key={`${ev.seq_no}-${ev.phase}`} className={`ev ${ev.level || "info"}`}>
                    [{ev.phase}] {ev.message}
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </section>
    </div>
    {schematicPortal}
    </>
  );
}
