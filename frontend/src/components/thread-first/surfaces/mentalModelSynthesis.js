/**
 * mentalModelSynthesis — derive a plain-English narrative + completeness
 * signal from the normalized mental-model data.
 *
 * First-principles motivation: the overlay's first answer must be "does
 * the system understand this?". A graph is necessary but insufficient —
 * humans need a sentence. This helper composes one from the data we
 * already have, so the user reads, in two beats:
 *   1. A picture of the design.
 *   2. A sentence that proves the system *gets* the picture.
 *
 * Returns:
 *   {
 *     headline    : string,   // one short sentence — "Your design centres on uart_top."
 *     summary     : string,   // composed paragraph of facts
 *     scopedTo    : string|null, // module name if scoped, else null
 *     completeness: {
 *       counts: { modules, ports, parameters, requirements, sourceFiles, clockDomains },
 *       label : string,   // "Read 4 RTL files · 12 ports · 7 requirements"
 *       tone  : "good"|"info"|"busy", // how "complete" the picture feels
 *     }
 *   }
 */

const ARTICLE_RE = /^(uart|ahb|apb|axi|i2c|spi|usb|pcie|ddr)$/i;

function articleFor(word) {
  if (!word) return "a";
  const w = String(word).trim();
  // Initialisms like UART, APB, I2C — these usually take "a" by sound
  // ("a UART", "an AHB") so we keep a sound-based heuristic on the first
  // pronounced char. Default to "a" since it reads better than "an" for
  // most module names ("a uart_top").
  if (ARTICLE_RE.test(w)) return "a";
  return /^[aeiou]/i.test(w) ? "an" : "a";
}

function pluralise(n, singular, plural) {
  return `${n} ${n === 1 ? singular : plural || `${singular}s`}`;
}

function joinNatural(items) {
  const arr = items.filter(Boolean);
  if (arr.length === 0) return "";
  if (arr.length === 1) return arr[0];
  if (arr.length === 2) return `${arr[0]} and ${arr[1]}`;
  return `${arr.slice(0, -1).join(", ")}, and ${arr[arr.length - 1]}`;
}

export function synthesiseMentalModel(data, { selectedNode = null } = {}) {
  if (!data) {
    return {
      headline: "No mental model yet.",
      summary: "Build the model from the current spec + RTL to see how the system understands your design.",
      scopedTo: null,
      completeness: {
        counts: { modules: 0, ports: 0, parameters: 0, requirements: 0, sourceFiles: 0, clockDomains: 0 },
        label: "Nothing read yet",
        tone: "busy",
      },
    };
  }

  const top = data.top_module || "";
  const modules = Array.isArray(data.modules) ? data.modules : [];
  const ports = Array.isArray(data.ports) ? data.ports : [];
  const parameters = Array.isArray(data.parameters) ? data.parameters : [];
  const requirements = Array.isArray(data.requirements) ? data.requirements : [];
  const sourceFiles = Array.isArray(data.source_files) ? data.source_files : [];
  const clockDomains = Array.isArray(data.clock_domains) ? data.clock_domains : [];
  const tree = data.hierarchy_tree || {};

  const counts = {
    modules: modules.length,
    ports: ports.length,
    parameters: parameters.length,
    requirements: requirements.length,
    sourceFiles: sourceFiles.length,
    clockDomains: clockDomains.length,
  };

  // Completeness label — surfaces the *evidence* the system has read.
  const completenessParts = [];
  if (counts.sourceFiles > 0) completenessParts.push(`read ${pluralise(counts.sourceFiles, "RTL file")}`);
  if (counts.modules > 0) completenessParts.push(pluralise(counts.modules, "module"));
  if (counts.ports > 0) completenessParts.push(pluralise(counts.ports, "port"));
  if (counts.requirements > 0) completenessParts.push(pluralise(counts.requirements, "requirement"));

  const completenessLabel = completenessParts.length
    ? completenessParts.join(" · ")
    : "Minimal evidence — try Rebuild";

  // Tone heuristic — how confident the picture looks.
  let tone = "busy";
  if (counts.modules >= 2 && counts.ports >= 1) tone = "good";
  else if (counts.modules >= 1) tone = "info";

  // Scoped synthesis: the user clicked a node, we should narrate that one.
  if (selectedNode) {
    const children = tree[selectedNode] || [];
    const parents = Object.entries(tree)
      .filter(([, kids]) => Array.isArray(kids) && kids.includes(selectedNode))
      .map(([k]) => k);
    const scopedPorts = ports.filter((p) => (
      p.module === selectedNode
      || p.module_name === selectedNode
      || p.parent_module === selectedNode
      || p.top_module === selectedNode
    ));
    const isTop = selectedNode === top;

    const lineageBits = [];
    if (parents.length > 0) lineageBits.push(`instantiated under ${joinNatural(parents)}`);
    if (children.length > 0) lineageBits.push(`contains ${joinNatural(children)}`);

    const summaryBits = [];
    summaryBits.push(`${selectedNode} is ${isTop ? "the top module" : "a submodule"}`);
    if (lineageBits.length > 0) summaryBits.push(lineageBits.join("; "));
    if (scopedPorts.length > 0) summaryBits.push(`${pluralise(scopedPorts.length, "port")} on its boundary`);

    return {
      headline: `${selectedNode} — ${isTop ? "the top module" : "a submodule"}.`,
      summary: `${summaryBits.join(". ")}.`,
      scopedTo: selectedNode,
      completeness: { counts, label: completenessLabel, tone },
    };
  }

  // Unscoped — describe the design as a whole.
  if (!top && modules.length === 0) {
    return {
      headline: "I haven't traced any modules yet.",
      summary: "Once we run a model build, I'll lay out the hierarchy and call out the moving parts.",
      scopedTo: null,
      completeness: { counts, label: completenessLabel, tone: "busy" },
    };
  }

  const article = articleFor(top || modules[0] || "design");
  const submodules = modules.filter((m) => m !== top);
  const submodulePreview = submodules.slice(0, 3);
  const submoduleRemainder = submodules.length - submodulePreview.length;

  const sentenceBits = [];
  if (top) {
    sentenceBits.push(`Your design centres on ${article} ${top}`);
  } else {
    sentenceBits.push(`Your design includes ${pluralise(modules.length, "module")}`);
  }

  if (submodules.length > 0) {
    const remainder = submoduleRemainder > 0
      ? `, plus ${submoduleRemainder} more`
      : "";
    sentenceBits.push(`with ${pluralise(submodules.length, "submodule")} — ${submodulePreview.join(", ")}${remainder}`);
  } else if (top) {
    sentenceBits.push("flat (no submodules instantiated)");
  }

  const detailBits = [];
  if (counts.ports > 0) detailBits.push(`${pluralise(counts.ports, "port")} on the top boundary`);
  if (counts.parameters > 0) detailBits.push(`${pluralise(counts.parameters, "parameter")}`);
  if (counts.clockDomains > 0) detailBits.push(`${pluralise(counts.clockDomains, "clock domain")}`);
  if (counts.requirements > 0) detailBits.push(`${pluralise(counts.requirements, "requirement")} extracted from the spec`);

  const headline = top
    ? `Your design centres on ${top}.`
    : `${pluralise(modules.length, "module")} in this project.`;

  const summary = [
    sentenceBits.join(", ") + ".",
    detailBits.length ? `I see ${joinNatural(detailBits)}.` : null,
  ].filter(Boolean).join(" ");

  return {
    headline,
    summary,
    scopedTo: null,
    completeness: { counts, label: completenessLabel, tone },
  };
}
