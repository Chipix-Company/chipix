"""
D3.js-based RTL Design Visualizer.
Generates interactive block diagrams from parsed Verilog module information.
"""

from __future__ import annotations
import json
from .parser import ModuleInfo, parse_verilog


def render_module_diagram(module: ModuleInfo) -> str:
    """
    Generate an HTML string containing a D3.js interactive block diagram
    for a single RTL module. Embeds in Streamlit via st.components.v1.html().
    """
    module_data = module.to_dict()
    module_json = json.dumps(module_data)

    html = f"""
<!DOCTYPE html>
<html>
<head>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: #0a0e17;
    font-family: 'Segoe UI', 'Inter', system-ui, sans-serif;
    overflow: hidden;
  }}
  svg {{
    width: 100%;
    height: 100%;
    cursor: grab;
  }}
  svg:active {{ cursor: grabbing; }}

  .module-box {{
    fill: #111827;
    stroke: #3b82f6;
    stroke-width: 2;
    rx: 12;
    filter: drop-shadow(0 4px 20px rgba(59, 130, 246, 0.3));
  }}
  .module-header {{
    fill: #1e3a5f;
    rx: 12;
  }}
  .module-header-bottom {{
    fill: #1e3a5f;
  }}
  .module-title {{
    fill: #60a5fa;
    font-size: 18px;
    font-weight: 700;
    text-anchor: middle;
    letter-spacing: 1px;
  }}
  .port-label {{
    font-size: 12px;
    font-weight: 500;
    letter-spacing: 0.5px;
  }}
  .port-label.input {{ fill: #4ade80; }}
  .port-label.output {{ fill: #f87171; }}
  .port-label.inout {{ fill: #fbbf24; }}

  .port-pin {{
    stroke-width: 2;
    cursor: pointer;
    transition: all 0.2s;
  }}
  .port-pin.input {{ fill: #4ade80; stroke: #22c55e; }}
  .port-pin.output {{ fill: #f87171; stroke: #ef4444; }}
  .port-pin.inout {{ fill: #fbbf24; stroke: #f59e0b; }}

  .port-wire {{
    stroke-width: 2;
    stroke-dasharray: none;
  }}
  .port-wire.input {{ stroke: #4ade80; }}
  .port-wire.output {{ stroke: #f87171; }}
  .port-wire.inout {{ stroke: #fbbf24; }}

  .width-label {{
    font-size: 10px;
    fill: #94a3b8;
    font-family: 'Consolas', 'Fira Code', monospace;
  }}

  .signal-box {{
    fill: #1e293b;
    stroke: #475569;
    stroke-width: 1;
    rx: 6;
  }}
  .signal-label {{
    font-size: 11px;
    fill: #cbd5e1;
    font-family: 'Consolas', 'Fira Code', monospace;
  }}

  .submodule-box {{
    fill: #1a1a2e;
    stroke: #8b5cf6;
    stroke-width: 2;
    rx: 8;
    filter: drop-shadow(0 2px 10px rgba(139, 92, 246, 0.2));
  }}
  .submodule-title {{
    fill: #a78bfa;
    font-size: 13px;
    font-weight: 600;
    text-anchor: middle;
  }}
  .submodule-inst {{
    fill: #7c3aed;
    font-size: 10px;
    text-anchor: middle;
    font-style: italic;
  }}

  .always-box {{
    rx: 6;
    stroke-width: 1.5;
  }}
  .always-box.sequential {{
    fill: #1e1b3a;
    stroke: #c084fc;
  }}
  .always-box.combinational {{
    fill: #1b2e1e;
    stroke: #86efac;
  }}
  .always-label {{
    font-size: 11px;
    font-weight: 500;
  }}
  .always-label.sequential {{ fill: #c084fc; }}
  .always-label.combinational {{ fill: #86efac; }}

  .legend-text {{
    font-size: 11px;
    fill: #94a3b8;
  }}
  .legend-dot {{
    stroke-width: 1.5;
  }}

  .tooltip {{
    position: absolute;
    background: #1e293b;
    border: 1px solid #3b82f6;
    border-radius: 8px;
    padding: 8px 12px;
    color: #e2e8f0;
    font-size: 12px;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.2s;
    z-index: 1000;
    box-shadow: 0 4px 20px rgba(0,0,0,0.5);
  }}
</style>
</head>
<body>
<div id="tooltip" class="tooltip"></div>
<svg id="diagram"></svg>

<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const moduleData = {module_json};

const svg = d3.select("#diagram");
const tooltip = d3.select("#tooltip");

// Dimensions
const width = 900;
const height = 650;
svg.attr("viewBox", `0 0 ${{width}} ${{height}}`);

// Zoom & pan
const g = svg.append("g");
const zoom = d3.zoom()
  .scaleExtent([0.3, 3])
  .on("zoom", (e) => g.attr("transform", e.transform));
svg.call(zoom);

// ─── Layout calculations ─────────────────────────────────────────
const inputPorts = moduleData.ports.filter(p => p.direction === "input");
const outputPorts = moduleData.ports.filter(p => p.direction === "output");
const inoutPorts = moduleData.ports.filter(p => p.direction === "inout");
const allLeftPorts = [...inputPorts, ...inoutPorts];

const portSpacing = 32;
const headerHeight = 45;
const paddingTop = 20;
const portAreaHeight = Math.max(allLeftPorts.length, outputPorts.length) * portSpacing + paddingTop * 2;

// Submodules & always blocks area
const submoduleCount = moduleData.submodules.length;
const alwaysCount = moduleData.always_blocks.length;
const internalSignalsCount = moduleData.internal_signals.length;
const extraHeight = Math.max(
  (submoduleCount * 70) + (alwaysCount * 50) + (internalSignalsCount > 0 ? 60 : 0),
  0
);

const boxWidth = 400;
const boxHeight = headerHeight + portAreaHeight + extraHeight + 40;
const boxX = (width - boxWidth) / 2;
const boxY = 60;
const wireLen = 70;

// ─── Module box ──────────────────────────────────────────────────
// Main box
g.append("rect")
  .attr("class", "module-box")
  .attr("x", boxX).attr("y", boxY)
  .attr("width", boxWidth).attr("height", boxHeight);

// Header background
g.append("rect")
  .attr("class", "module-header")
  .attr("x", boxX).attr("y", boxY)
  .attr("width", boxWidth).attr("height", headerHeight);
g.append("rect")
  .attr("class", "module-header-bottom")
  .attr("x", boxX).attr("y", boxY + headerHeight - 12)
  .attr("width", boxWidth).attr("height", 12);

// Title
g.append("text")
  .attr("class", "module-title")
  .attr("x", boxX + boxWidth / 2)
  .attr("y", boxY + headerHeight / 2 + 6)
  .text(moduleData.name);

// ─── Parameters ──────────────────────────────────────────────────
if (moduleData.parameters.length > 0) {{
  const paramText = moduleData.parameters.map(p => `${{p.name}}=${{p.value}}`).join(", ");
  g.append("text")
    .attr("x", boxX + boxWidth / 2)
    .attr("y", boxY - 10)
    .attr("text-anchor", "middle")
    .attr("fill", "#94a3b8")
    .attr("font-size", "11px")
    .attr("font-family", "'Consolas', monospace")
    .text(`#( ${{paramText}} )`);
}}

// ─── Ports ───────────────────────────────────────────────────────
function drawPort(port, x, y, side) {{
  const isLeft = side === "left";
  const wireStartX = isLeft ? x - wireLen : x + wireLen;
  const pinX = isLeft ? x - wireLen - 6 : x + wireLen + 6;

  // Wire
  g.append("line")
    .attr("class", `port-wire ${{port.direction}}`)
    .attr("x1", wireStartX).attr("y1", y)
    .attr("x2", x).attr("y2", y);

  // Pin (diamond shape)
  const pinSize = 5;
  g.append("polygon")
    .attr("class", `port-pin ${{port.direction}}`)
    .attr("points", `${{pinX}},${{y-pinSize}} ${{pinX+pinSize}},${{y}} ${{pinX}},${{y+pinSize}} ${{pinX-pinSize}},${{y}}`)
    .on("mouseover", (event) => {{
      tooltip.style("opacity", 1)
        .html(`<b>${{port.name}}</b><br>Dir: ${{port.direction}}<br>Width: ${{port.width}}${{port.port_type ? '<br>Type: ' + port.port_type : ''}}`)
        .style("left", (event.pageX + 15) + "px")
        .style("top", (event.pageY - 10) + "px");
    }})
    .on("mouseout", () => tooltip.style("opacity", 0));

  // Label
  const labelX = isLeft ? wireStartX - 14 : wireStartX + 14;
  g.append("text")
    .attr("class", `port-label ${{port.direction}}`)
    .attr("x", labelX).attr("y", y + 4)
    .attr("text-anchor", isLeft ? "end" : "start")
    .text(port.name);

  // Width annotation
  if (port.width && port.width !== "1") {{
    g.append("text")
      .attr("class", "width-label")
      .attr("x", (wireStartX + x) / 2)
      .attr("y", y - 8)
      .attr("text-anchor", "middle")
      .text(port.width);
  }}
}}

// Draw left ports (inputs + inout)
allLeftPorts.forEach((port, i) => {{
  const y = boxY + headerHeight + paddingTop + i * portSpacing + portSpacing / 2;
  drawPort(port, boxX, y, "left");
}});

// Draw right ports (outputs)
outputPorts.forEach((port, i) => {{
  const y = boxY + headerHeight + paddingTop + i * portSpacing + portSpacing / 2;
  drawPort(port, boxX + boxWidth, y, "right");
}});

// ─── Internal signals ────────────────────────────────────────────
const internalStartY = boxY + headerHeight + portAreaHeight + 10;

if (moduleData.internal_signals.length > 0) {{
  const sigBoxX = boxX + 15;
  const sigBoxW = boxWidth - 30;
  const sigBoxH = 45;

  g.append("rect")
    .attr("class", "signal-box")
    .attr("x", sigBoxX).attr("y", internalStartY)
    .attr("width", sigBoxW).attr("height", sigBoxH);

  const sigText = moduleData.internal_signals.map(s =>
    `${{s.signal_type}} ${{s.width !== '1' ? s.width + ' ' : ''}}${{s.name}}`
  ).join(", ");

  g.append("text")
    .attr("class", "signal-label")
    .attr("x", sigBoxX + 10)
    .attr("y", internalStartY + 18)
    .text("Signals: " + (sigText.length > 55 ? sigText.substring(0, 52) + "..." : sigText));

  g.append("text")
    .attr("x", sigBoxX + 10)
    .attr("y", internalStartY + 35)
    .attr("fill", "#64748b")
    .attr("font-size", "10px")
    .text(`${{moduleData.internal_signals.length}} internal signal(s)`);
}}

// ─── Submodules ──────────────────────────────────────────────────
let currentY = internalStartY + (moduleData.internal_signals.length > 0 ? 60 : 0);

moduleData.submodules.forEach((sub, i) => {{
  const subX = boxX + 30;
  const subW = boxWidth - 60;
  const subH = 55;

  g.append("rect")
    .attr("class", "submodule-box")
    .attr("x", subX).attr("y", currentY)
    .attr("width", subW).attr("height", subH);

  g.append("text")
    .attr("class", "submodule-title")
    .attr("x", subX + subW / 2)
    .attr("y", currentY + 22)
    .text(sub.module_type);

  g.append("text")
    .attr("class", "submodule-inst")
    .attr("x", subX + subW / 2)
    .attr("y", currentY + 42)
    .text(`instance: ${{sub.instance_name}}`);

  currentY += subH + 15;
}});

// ─── Always blocks ───────────────────────────────────────────────
moduleData.always_blocks.forEach((ab, i) => {{
  const abX = boxX + 30;
  const abW = boxWidth - 60;
  const abH = 35;

  g.append("rect")
    .attr("class", `always-box ${{ab.block_type}}`)
    .attr("x", abX).attr("y", currentY)
    .attr("width", abW).attr("height", abH);

  const icon = ab.block_type === "sequential" ? "⏱" : "⚡";
  g.append("text")
    .attr("class", `always-label ${{ab.block_type}}`)
    .attr("x", abX + 12)
    .attr("y", currentY + 23)
    .text(`${{icon}} ${{ab.description}} @ (${{ab.sensitivity}})`);

  currentY += abH + 10;
}});

// ─── Legend ──────────────────────────────────────────────────────
const legendData = [
  {{ color: "#4ade80", stroke: "#22c55e", label: "Input" }},
  {{ color: "#f87171", stroke: "#ef4444", label: "Output" }},
  {{ color: "#fbbf24", stroke: "#f59e0b", label: "Inout" }},
  {{ color: "#a78bfa", stroke: "#8b5cf6", label: "Submodule" }},
  {{ color: "#c084fc", stroke: "#c084fc", label: "Sequential" }},
  {{ color: "#86efac", stroke: "#86efac", label: "Combinational" }},
];

const legendG = g.append("g").attr("transform", `translate(${{boxX}}, ${{boxY + boxHeight + 25}})`);
legendData.forEach((item, i) => {{
  const lx = (i % 3) * 150;
  const ly = Math.floor(i / 3) * 22;
  legendG.append("circle")
    .attr("class", "legend-dot")
    .attr("cx", lx + 6).attr("cy", ly + 6)
    .attr("r", 5)
    .attr("fill", item.color)
    .attr("stroke", item.stroke);
  legendG.append("text")
    .attr("class", "legend-text")
    .attr("x", lx + 18).attr("y", ly + 10)
    .text(item.label);
}});

// Center the view
svg.call(zoom.transform, d3.zoomIdentity.translate(0, 0).scale(0.95));
</script>
</body>
</html>
"""
    return html


def render_all_modules(code: str) -> list[tuple[str, str]]:
    """
    Parse Verilog code and render block diagrams for all modules found.
    Returns a list of (module_name, html_string) tuples.
    """
    modules = parse_verilog(code)
    results = []
    for module in modules:
        html = render_module_diagram(module)
        results.append((module.name, html))
    return results
