import { inferMonacoLanguage } from "./ideEditorUtils";

/** Light RTL/SystemVerilog tidy - not a full parser, but cleans common noise. */
function normalizeVerilogLine(line = "") {
  return String(line)
    .replace(/\s+$/g, "")
    .replace(/\balways\s*@/gi, "always @")
    .replace(/\b(if|case|for|while)\s*\(/gi, "$1 (")
    .replace(/\bbegin\s*:\s*/gi, "begin : ")
    .replace(/^([A-Za-z_]\w*)\s*:\s*begin\b/i, "$1: begin")
    .replace(/\s*(===|!==|==|!=|<=|>=|&&|\|\|)\s*/g, " $1 ")
    .replace(/([^<>=!+\-*/%&|^~?:\s])\s*=\s*([^=])/g, "$1 = $2")
    .replace(/\s{2,}/g, " ")
    .replace(/\s+;/g, ";");
}

function closesBlock(trimmed = "") {
  return /^(end|endcase|endfunction|endtask|endmodule|endinterface|endpackage|endclass|join|join_any|join_none)\b/i
    .test(trimmed);
}

function opensBlock(trimmed = "") {
  if (!trimmed || /^\/\//.test(trimmed)) return false;
  if (closesBlock(trimmed) && !/\belse\b.*\bbegin\b/i.test(trimmed)) return false;
  if (/\bbegin\b/i.test(trimmed)) return true;
  if (/^(module|interface|package|program|class|function|task|case|casex|casez|fork|generate|covergroup)\b/i.test(trimmed)) {
    return !/;\s*(\/\/.*)?$/.test(trimmed);
  }
  return false;
}

export function formatVerilogLoose(source = "") {
  const lines = String(source).replace(/\r\n?/g, "\n").replace(/\t/g, "  ").split("\n");
  const out = [];
  let depth = 0;
  for (let raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      if (out.length && out[out.length - 1] !== "") out.push("");
      continue;
    }
    const trimmed = normalizeVerilogLine(line.trim());
    if (closesBlock(trimmed)) {
      depth = Math.max(0, depth - 1);
    }
    out.push(`${"  ".repeat(depth)}${trimmed}`);
    if (opensBlock(trimmed)) {
      depth += 1;
    }
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n").trimEnd() + (out.length ? "\n" : "");
}

function prettierParserForLanguage(language) {
  switch (language) {
    case "json": return "json";
    case "markdown": return "markdown";
    case "javascript": return "babel";
    case "typescript": return "typescript";
    case "yaml": return "yaml";
    case "html": return "html";
    case "css": return "css";
    default: return null;
  }
}

export async function formatEditorContent(content, filename = "") {
  const language = inferMonacoLanguage(filename);
  if (language === "systemverilog" || language === "plaintext" && /\.(sv|svh|v|vh)$/i.test(filename)) {
    return formatVerilogLoose(content);
  }

  const parser = prettierParserForLanguage(language);
  if (!parser) return content;

  try {
    const prettier = await import("prettier/standalone");
    const estree = await import("prettier/plugins/estree");
    const babel = await import("prettier/plugins/babel");
    const markdown = await import("prettier/plugins/markdown");
    const yaml = await import("prettier/plugins/yaml");
    const html = await import("prettier/plugins/html");
    const postcss = await import("prettier/plugins/postcss");
    const typescript = await import("prettier/plugins/typescript");

    const plugins = [estree, babel, markdown, yaml, html, postcss, typescript];

    return await prettier.format(String(content), {
      parser,
      plugins,
      printWidth: 100,
      tabWidth: 2,
    });
  } catch {
    return content;
  }
}
