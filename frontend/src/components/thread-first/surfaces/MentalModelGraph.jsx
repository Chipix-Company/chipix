import React, { useMemo } from "react";
import Icon from "../icons";

/**
 * MentalModelGraph — hierarchy / data-flow SVG graph.
 *
 * Edges are drawn as SVG inside an absolutely-positioned <svg>.
 * Nodes are real HTML <button>s overlaid on top of the SVG so they
 * are keyboard-focusable and a11y-friendly (aria-pressed).
 *
 * Visual physics (Slice 2 polish):
 *   - Nodes enter level-by-level via a CSS animation keyed by
 *     `--tf-mm-stagger`. Top module appears first; submodules ripple
 *     down. Gives the feel of "I'm drawing this out for you".
 *   - Nodes lift slightly on hover and bloom a small mode-tinted glow.
 *   - When a node is selected, edges directly touching it are marked
 *     `connected` (CSS highlights them) and unrelated nodes/edges
 *     dim back so the eye traces the lineage.
 */
export default function MentalModelGraph({ data, selectedNode, onSelectNode }) {
  const layout = useMemo(() => layoutHierarchy(data), [data]);

  if (!layout.nodes.length) {
    return (
      <div className="tf-mm-graph-empty">
        <Icon.Layers width="20" height="20" />
        <span>No hierarchy yet — the mental model has not been built.</span>
      </div>
    );
  }

  const { nodes, edges, width, height } = layout;

  // Compute which edges and nodes are "connected" to the current selection.
  // A node is connected iff it equals selectedNode OR is an immediate
  // neighbour (parent or child) of it. An edge is connected iff either of
  // its endpoints is the selected node.
  const connectedSet = new Set();
  if (selectedNode) {
    connectedSet.add(selectedNode);
    edges.forEach((edge) => {
      if (edge.parent === selectedNode) connectedSet.add(edge.child);
      else if (edge.child === selectedNode) connectedSet.add(edge.parent);
    });
  }

  const hasSelection = Boolean(selectedNode);

  return (
    <div
      className={`tf-mm-graph-wrap${hasSelection ? " has-selection" : ""}`}
      style={{ aspectRatio: `${width} / ${height}` }}
      role="group"
      aria-label="Design hierarchy"
    >
      <svg
        className="tf-mm-graph-svg"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        aria-hidden="true"
      >
        <defs>
          <marker
            id="tf-mm-arrow"
            viewBox="0 0 10 10"
            refX="10"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto"
          >
            <path d="M0,0 L10,5 L0,10 z" />
          </marker>
          <marker
            id="tf-mm-arrow-hot"
            viewBox="0 0 10 10"
            refX="10"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto"
          >
            <path d="M0,0 L10,5 L0,10 z" className="tf-mm-graph-arrow-hot" />
          </marker>
        </defs>
        {edges.map((edge, i) => {
          const isConnected = selectedNode
            && (edge.parent === selectedNode || edge.child === selectedNode);
          return (
            <line
              key={`e-${i}`}
              x1={edge.x1}
              y1={edge.y1}
              x2={edge.x2}
              y2={edge.y2}
              className={`tf-mm-graph-edge${isConnected ? " connected" : ""}`}
              markerEnd={isConnected ? "url(#tf-mm-arrow-hot)" : "url(#tf-mm-arrow)"}
            />
          );
        })}
      </svg>
      <div className="tf-mm-graph-nodes">
        {nodes.map((node, i) => {
          const isSelected = selectedNode === node.name;
          const isTop = node.level === 0;
          const isConnected = hasSelection && connectedSet.has(node.name);
          const isDim = hasSelection && !isConnected;
          return (
            <button
              key={node.name}
              type="button"
              className={`tf-mm-graph-node${isSelected ? " selected" : ""}${isTop ? " top" : ""}${isConnected ? " connected" : ""}${isDim ? " dim" : ""}`}
              aria-pressed={isSelected}
              data-level={node.level}
              data-index={i}
              style={{
                left: `${(node.x / width) * 100}%`,
                top: `${(node.y / height) * 100}%`,
                width: `${(node.width / width) * 100}%`,
                "--tf-mm-stagger": `${node.level * 80 + (i - node.level) * 12}ms`,
              }}
              onClick={() => onSelectNode?.(node.name)}
              title={node.name}
            >
              <span className="tf-mm-graph-node-name">{node.name}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function layoutHierarchy(data) {
  const nodes = [];
  const edges = [];
  const tree = (data && data.hierarchy_tree) || {};
  const allModules = (data && data.modules) || [];
  const topModule = (data && data.top_module) || "";

  if (allModules.length === 0 && !topModule) {
    return { nodes: [], edges: [], width: 100, height: 100 };
  }

  const levels = [];
  const visited = new Set();
  const positions = {};

  const root = topModule || allModules[0];
  const queue = [root];
  visited.add(root);

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

  const orphans = allModules.filter((m) => !visited.has(m));
  if (orphans.length > 0) levels.push(orphans);

  const NODE_H = 36;
  const NODE_GAP_X = 22;
  const LEVEL_GAP = 84;
  const CHAR_WIDTH = 8.5;
  const MIN_NODE_W = 96;
  const PAD_X = 36;
  const PAD_Y = 32;

  let maxLevelWidth = 0;
  const sized = levels.map((level) => {
    const items = level.map((name) => ({
      name,
      width: Math.max(MIN_NODE_W, name.length * CHAR_WIDTH + 28),
    }));
    const total =
      items.reduce((s, it) => s + it.width, 0) +
      Math.max(0, items.length - 1) * NODE_GAP_X;
    maxLevelWidth = Math.max(maxLevelWidth, total);
    return { items, total };
  });

  sized.forEach(({ items, total }, levelIdx) => {
    const y = PAD_Y + levelIdx * LEVEL_GAP + NODE_H / 2;
    let x = PAD_X + (maxLevelWidth - total) / 2;
    items.forEach((it) => {
      const cx = x + it.width / 2;
      nodes.push({
        name: it.name,
        x: cx,
        y,
        width: it.width,
        level: levelIdx,
      });
      positions[it.name] = { x: cx, y };
      x += it.width + NODE_GAP_X;
    });
  });

  const edgeKeys = new Set();
  const pushEdge = (parent, child) => {
    if (!parent || !child || parent === child) return;
    const key = `${parent}→${child}`;
    if (edgeKeys.has(key)) return;
    const pp = positions[parent];
    const cp = positions[child];
    if (!pp || !cp) return;
    edgeKeys.add(key);
    edges.push({
      parent,
      child,
      x1: pp.x,
      y1: pp.y + NODE_H / 2,
      x2: cp.x,
      y2: cp.y - NODE_H / 2,
    });
  };

  for (const [parent, children] of Object.entries(tree)) {
    for (const child of children) pushEdge(parent, child);
  }

  // Orphan modules often lack tree edges — wire from sub_instances when present.
  for (const inst of data.sub_instances || []) {
    const child = inst.module_name || inst.module;
    const parent =
      inst.parent_module
      || inst.parent
      || (inst.instance_name && positions[topModule] ? topModule : null)
      || topModule;
    pushEdge(parent, child);
  }

  const width = Math.max(maxLevelWidth + PAD_X * 2, 320);
  const height = sized.length * LEVEL_GAP + PAD_Y;

  return { nodes, edges, width, height };
}
