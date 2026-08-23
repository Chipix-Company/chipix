/** Monaco themes aligned with thread-first tokens — editor slightly darker than chrome. */
export const CHIPIX_MONACO_THEMES = {
  dark: "chipix-dark",
  light: "chipix-light",
};

export function defineChipixMonacoThemes(monaco) {
  if (!monaco?.editor?.defineTheme) return;

  monaco.editor.defineTheme(CHIPIX_MONACO_THEMES.dark, {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "comment", foreground: "6d6860", fontStyle: "italic" },
      { token: "keyword", foreground: "c9a86c", fontStyle: "bold" },
      { token: "string", foreground: "9cb896" },
      { token: "number", foreground: "8ab4d4" },
      { token: "type", foreground: "b8a8d8" },
      { token: "tag", foreground: "c9a86c" },
      { token: "attribute.name", foreground: "d4c4a0" },
      { token: "delimiter", foreground: "a8a295" },
    ],
    colors: {
      "editor.background": "#100e0b",
      "editor.foreground": "#ece8e0",
      "editorLineNumber.foreground": "#5a554c",
      "editorLineNumber.activeForeground": "#9a9488",
      "editor.lineHighlightBackground": "#1a1713",
      "editor.lineHighlightBorder": "#1a171300",
      "editor.selectionBackground": "#3a352c88",
      "editor.inactiveSelectionBackground": "#3a352c44",
      "editorCursor.foreground": "#ece8e0",
      "editorWhitespace.foreground": "#2e2a24",
      "editorIndentGuide.background": "#25221c",
      "editorIndentGuide.activeBackground": "#3a352c",
      "editorGutter.background": "#100e0b",
      "editorWidget.background": "#1c1a16",
      "editorWidget.border": "#2e2a24",
      "scrollbarSlider.background": "#3a352c88",
      "scrollbarSlider.hoverBackground": "#4a443a99",
      "minimap.background": "#100e0b",
    },
  });

  monaco.editor.defineTheme(CHIPIX_MONACO_THEMES.light, {
    base: "vs",
    inherit: true,
    rules: [
      { token: "comment", foreground: "8a8478", fontStyle: "italic" },
      { token: "keyword", foreground: "8a6a3e", fontStyle: "bold" },
      { token: "string", foreground: "4a7050" },
      { token: "number", foreground: "3a6080" },
    ],
    colors: {
      "editor.background": "#f3f0ea",
      "editor.foreground": "#1c1a16",
      "editorLineNumber.foreground": "#b8b0a4",
      "editorLineNumber.activeForeground": "#6a6458",
      "editor.lineHighlightBackground": "#ebe6dc",
      "editor.selectionBackground": "#d4cbb888",
      "editorCursor.foreground": "#1c1a16",
      "editorGutter.background": "#f3f0ea",
      "editorIndentGuide.background": "#e4ddd0",
      "scrollbarSlider.background": "#c8bfb088",
    },
  });
}

export function registerSystemVerilogLanguage(monaco) {
  if (!monaco?.languages?.register) return;
  const id = "systemverilog";
  if (monaco.languages.getLanguages().some((l) => l.id === id)) return;

  monaco.languages.register({ id });

  monaco.languages.setMonarchTokensProvider(id, {
    defaultToken: "",
    ignoreCase: false,
    keywords: [
      "module", "endmodule", "interface", "endinterface", "package", "endpackage",
      "input", "output", "inout", "wire", "reg", "logic", "bit", "int", "integer",
      "parameter", "localparam", "assign", "always", "always_ff", "always_comb",
      "always_latch", "initial", "begin", "end", "if", "else", "case", "endcase",
      "for", "while", "function", "endfunction", "task", "endtask", "posedge", "negedge",
      "import", "typedef", "struct", "enum", "generate", "endgenerate",
    ],
    operators: [
      "=", "==", "!=", "<", "<=", ">", ">=", "+", "-", "*", "/", "%",
      "&", "|", "^", "~", "&&", "||", "!", "?", ":", ";", ",", ".",
    ],
    symbols: /[=><!~?:&|+\-*/^%]+/,
    tokenizer: {
      root: [
        [/\/\/.*$/, "comment"],
        [/\/\*[\s\S]*?\*\//, "comment"],
        [/"([^"\\]|\\.)*$/, "string.invalid"],
        [/"/, "string", "@string"],
        [/`[^`]+`/, "type"],
        [/\b\d+'[bhdBHD][\w_xXzZ?]+|\b\d+/, "number"],
        [/[a-zA-Z_]\w*/, {
          cases: {
            "@keywords": "keyword",
            "@default": "identifier",
          },
        }],
        [/[{}()[\]]/, "@brackets"],
        [/@symbols/, { cases: { "@operators": "operator", "@default": "" } }],
      ],
      string: [
        [/[^\\"]+/, "string"],
        [/\\./, "string.escape"],
        [/"/, "string", "@pop"],
      ],
    },
  });
}

export function setupChipixMonaco(monaco) {
  defineChipixMonacoThemes(monaco);
  registerSystemVerilogLanguage(monaco);
}
