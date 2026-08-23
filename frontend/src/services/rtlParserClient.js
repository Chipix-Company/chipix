const MODULE_RE =
  /module\s+([a-zA-Z_]\w*)\s*(?:#\s*\([\s\S]*?\))?\s*\(([\s\S]*?)\)\s*;([\s\S]*?)endmodule/gm;
const PORT_RE =
  /\b(input|output|inout)\b\s*(?:wire|reg|logic|signed|unsigned)?\s*(\[[^\]]+\])?\s*([a-zA-Z_]\w*)/g;
const INSTANCE_RE =
  /^\s*([a-zA-Z_]\w*)\s+(?:#\s*\([\s\S]*?\)\s*)?([a-zA-Z_]\w*)\s*\(/gm;

function lineRangeForIndex(source, startIndex, endIndex = startIndex) {
  const beforeStart = source.slice(0, startIndex);
  const beforeEnd = source.slice(0, endIndex);
  return {
    startLine: beforeStart.split("\n").length,
    endLine: beforeEnd.split("\n").length,
  };
}

function buildNodeId(parts) {
  return parts.filter(Boolean).join(":");
}

function parseModulesFallback(source) {
  const modules = [];
  let moduleMatch;
  while ((moduleMatch = MODULE_RE.exec(source)) !== null) {
    const moduleName = moduleMatch[1];
    const header = moduleMatch[2] || "";
    const body = moduleMatch[3] || "";
    const absoluteStart = moduleMatch.index;
    const absoluteEnd = moduleMatch.index + moduleMatch[0].length;
    const ports = [];
    const instances = [];
    const signals = [];

    let portMatch;
    PORT_RE.lastIndex = 0;
    while ((portMatch = PORT_RE.exec(`${header}\n${body}`)) !== null) {
      const portName = portMatch[3];
      const range = lineRangeForIndex(source, absoluteStart + portMatch.index);
      ports.push({
        id: buildNodeId(["port", moduleName, portName]),
        kind: "port",
        label: portName,
        name: portName,
        moduleName,
        direction: portMatch[1].toLowerCase(),
        width: portMatch[2] || "",
        range,
      });
      signals.push({
        id: buildNodeId(["signal", moduleName, portName]),
        kind: "signal",
        label: portName,
        name: portName,
        moduleName,
        range,
      });
    }

    let instanceMatch;
    INSTANCE_RE.lastIndex = 0;
    while ((instanceMatch = INSTANCE_RE.exec(body)) !== null) {
      const targetModuleName = instanceMatch[1];
      const instanceName = instanceMatch[2];
      if (targetModuleName === moduleName) {
        continue;
      }
      instances.push({
        id: buildNodeId(["instance", moduleName, instanceName]),
        kind: "instance",
        label: instanceName,
        moduleName,
        instanceName,
        targetModuleName,
        range: lineRangeForIndex(source, absoluteStart + instanceMatch.index),
      });
    }

    modules.push({
      id: buildNodeId(["module", moduleName]),
      kind: "module",
      label: moduleName,
      moduleName,
      range: lineRangeForIndex(source, absoluteStart, absoluteEnd),
      ports,
      instances,
      signals,
    });
  }

  return modules;
}

async function tryTreeSitterParse(source) {
  try {
    const treeSitterModule = await new Function(
      'return import("web-tree-sitter")',
    )();
    const Parser = treeSitterModule.Parser || treeSitterModule.default;
    if (!Parser?.init) {
      throw new Error("web-tree-sitter package loaded without Parser.init");
    }
    return {
      parserBackend: "tree-sitter-unconfigured",
      diagnostics: [
        {
          severity: "warning",
          message:
            "web-tree-sitter is installed but no SystemVerilog grammar wasm has been bundled yet. Falling back to structural parsing.",
        },
      ],
      treeSitterReady: false,
    };
  } catch (error) {
    return {
      parserBackend: "regex-fallback",
      diagnostics: [
        {
          severity: "warning",
          message:
            error?.message?.includes("Failed to fetch dynamically imported module")
              ? "web-tree-sitter is not installed in the current frontend environment. Falling back to structural parsing."
              : "Tree-sitter parser assets are unavailable. Falling back to structural parsing.",
        },
      ],
      treeSitterReady: false,
    };
  }
}

export async function parseRtlSource(source, _previousTree = null) {
  const sourceText = String(source || "");
  const modules = parseModulesFallback(sourceText);
  const treeSitterAttempt = await tryTreeSitterParse(sourceText);
  const nodeIndex = {};
  const hierarchy = modules.map((module) => {
    nodeIndex[module.id] = module;
    module.ports.forEach((port) => {
      nodeIndex[port.id] = port;
    });
    module.instances.forEach((instance) => {
      nodeIndex[instance.id] = instance;
    });
    module.signals.forEach((signal) => {
      nodeIndex[signal.id] = signal;
    });
    return {
      id: module.id,
      kind: module.kind,
      label: module.label,
      moduleName: module.moduleName,
      range: module.range,
      children: [
        ...module.instances.map((instance) => ({
          id: instance.id,
          kind: instance.kind,
          label: instance.label,
          moduleName: instance.moduleName,
          instanceName: instance.instanceName,
          targetModuleName: instance.targetModuleName,
          range: instance.range,
          children: [],
        })),
        ...module.ports.map((port) => ({
          id: port.id,
          kind: port.kind,
          label: port.label,
          moduleName: port.moduleName,
          signalName: port.name,
          direction: port.direction,
          range: port.range,
          children: [],
        })),
      ],
    };
  });

  return {
    parserBackend: treeSitterAttempt.parserBackend,
    treeSitterReady: treeSitterAttempt.treeSitterReady,
    diagnostics: [
      ...treeSitterAttempt.diagnostics,
      ...(modules.length === 0
        ? [
            {
              severity: "warning",
              message: "No SystemVerilog modules were detected in the current buffer.",
            },
          ]
        : []),
    ],
    sourceHash: `${sourceText.length}:${modules.length}`,
    hierarchy,
    modules,
    nodeIndex,
    topModuleName: modules[0]?.moduleName || "",
  };
}

export function findNodeForEditorSelection(snapshot, editorSelection) {
  if (!snapshot?.modules?.length || !editorSelection) {
    return null;
  }
  const currentLine = editorSelection.startLine || 1;
  for (const module of snapshot.modules) {
    if (
      currentLine >= module.range.startLine &&
      currentLine <= module.range.endLine
    ) {
      for (const instance of module.instances) {
        if (
          currentLine >= instance.range.startLine &&
          currentLine <= instance.range.endLine
        ) {
          return instance;
        }
      }
      for (const port of module.ports) {
        if (
          currentLine >= port.range.startLine &&
          currentLine <= port.range.endLine
        ) {
          return port;
        }
      }
      return module;
    }
  }
  return null;
}



