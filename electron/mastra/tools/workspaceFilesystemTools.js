/**
 * workspaceFilesystemTools.js
 * ============================
 * Mastra-native file operation tools backed by @mastra/core/workspace LocalFilesystem.
 * These complement the bash tool for structured, schema-validated file ops.
 *
 * Tools provided:
 *  - workspace_list_files  : list directory contents with structured output
 *  - workspace_read_file   : read a file with line-number awareness
 *  - workspace_write_file  : write/create a file (replaces full content)
 *  - workspace_grep        : search file contents with regex
 */

const { z } = require("zod");
const fsSync = require("node:fs");
const fs = require("node:fs/promises");
const path = require("node:path");

async function loadCreateTool() {
  const mod = await import("@mastra/core/tools");
  return mod.createTool || mod.default?.createTool || mod.default;
}

const MAX_FILE_CHARS = 40_000; // ~10k tokens

async function createWorkspaceFilesystemTools({ workspaceRoot } = {}) {
  const createTool = await loadCreateTool();
  const root = path.resolve(
    workspaceRoot || process.env.CHIPVERIFY_WORKSPACE_ROOT || process.cwd(),
  );

  if (!fsSync.existsSync(root)) {
    fsSync.mkdirSync(root, { recursive: true });
  }

  /** Resolve a user-provided path safely within root */
  function safeResolve(userPath) {
    const resolved = path.resolve(root, userPath.replace(/^\/+/, ""));
    // Prevent path traversal outside workspace
    if (!resolved.startsWith(root)) {
      throw new Error(`Path '${userPath}' is outside the workspace root.`);
    }
    return resolved;
  }

  // ── workspace_list_files ─────────────────────────────────────────────────
  const listFiles = createTool({
    id: "workspace_list_files",
    description:
      `List files and directories in the local workspace (root: ${root}). ` +
      "Returns name, type (file/dir), and size. Use path='.' for the workspace root.",
    inputSchema: z.object({
      path: z
        .string()
        .default(".")
        .describe("Relative path inside the workspace to list. Use '.' for root."),
      recursive: z
        .boolean()
        .default(false)
        .describe("If true, list all files recursively."),
      pattern: z
        .string()
        .optional()
        .describe("Optional glob pattern to filter results, e.g. '*.sv', '*.js'."),
    }),
    execute: async ({ path: userPath, recursive, pattern }) => {
      try {
        const resolvedPath = safeResolve(userPath || ".");

        // Check if it even exists
        const stat = await fs.stat(resolvedPath).catch(() => null);
        if (!stat) {
          return {
            success: false,
            output: `Path '${userPath}' does not exist in workspace at ${root}`,
          };
        }

        // Build list recursively or flat
        async function collectEntries(dirPath, depth = 0) {
          const entries = await fs.readdir(dirPath, { withFileTypes: true });
          const results = [];
          for (const entry of entries) {
            // Skip hidden dirs and node_modules for performance
            if (entry.name.startsWith(".") || entry.name === "node_modules") continue;

            const fullPath = path.join(dirPath, entry.name);
            const relPath = path.relative(root, fullPath);

            if (entry.isDirectory()) {
              results.push({ name: entry.name, type: "directory", path: relPath });
              if (recursive && depth < 6) {
                results.push(...await collectEntries(fullPath, depth + 1));
              }
            } else if (entry.isFile()) {
              // Apply pattern filter if provided
              if (pattern) {
                const { minimatch } = await import("minimatch").catch(() => ({ minimatch: null }));
                if (minimatch && !minimatch(entry.name, pattern)) continue;
                if (!minimatch) {
                  // Fallback: simple extension match
                  const ext = pattern.replace(/^\*/, "");
                  if (!entry.name.endsWith(ext)) continue;
                }
              }
              const fileStat = await fs.stat(fullPath).catch(() => null);
              results.push({
                name: entry.name,
                type: "file",
                path: relPath,
                size: fileStat?.size ?? 0,
              });
            }
          }
          return results;
        }

        const entries = await collectEntries(resolvedPath);
        const dirs = entries.filter((e) => e.type === "directory");
        const files = entries.filter((e) => e.type === "file");

        return {
          success: true,
          workspaceRoot: root,
          listedPath: path.relative(root, resolvedPath) || ".",
          totalFiles: files.length,
          totalDirs: dirs.length,
          entries,
          output:
            entries.length === 0
              ? `No files found in '${userPath}'`
              : entries
                  .slice(0, 500)
                  .map((e) => `${e.type === "directory" ? "📁" : "📄"} ${e.path}${e.size != null && e.type === "file" ? ` (${e.size}b)` : ""}`)
                  .join("\n"),
        };
      } catch (err) {
        return { success: false, output: `Error listing files: ${err?.message}` };
      }
    },
  });

  // ── workspace_read_file ──────────────────────────────────────────────────
  const readFile = createTool({
    id: "workspace_read_file",
    description:
      `Read the content of a file from the local workspace (root: ${root}). ` +
      "Use bash tool's 'cat' for quick reads; use this for structured metadata.",
    inputSchema: z.object({
      path: z.string().describe("Relative path to the file inside the workspace."),
      startLine: z.number().optional().describe("Start line (1-indexed). Omit to read from start."),
      endLine: z.number().optional().describe("End line (1-indexed). Omit to read to end."),
    }),
    execute: async ({ path: userPath, startLine, endLine }) => {
      try {
        const resolvedPath = safeResolve(userPath);
        const stat = await fs.stat(resolvedPath).catch(() => null);

        if (!stat) {
          return { success: false, output: `File '${userPath}' not found.` };
        }
        if (!stat.isFile()) {
          return { success: false, output: `'${userPath}' is not a file (it's a directory).` };
        }

        const raw = await fs.readFile(resolvedPath, "utf8");
        const lines = raw.split("\n");
        const totalLines = lines.length;

        const sl = startLine ? Math.max(1, startLine) : 1;
        const el = endLine ? Math.min(totalLines, endLine) : totalLines;
        const slice = lines.slice(sl - 1, el).join("\n");

        const content = slice.length > MAX_FILE_CHARS
          ? slice.slice(0, MAX_FILE_CHARS) + `\n...[truncated at ${MAX_FILE_CHARS} chars]`
          : slice;

        return {
          success: true,
          path: userPath,
          totalLines,
          shownLines: { start: sl, end: el },
          sizeBytes: stat.size,
          content,
          output: content,
        };
      } catch (err) {
        return { success: false, output: `Error reading file: ${err?.message}` };
      }
    },
  });

  // ── workspace_write_file ─────────────────────────────────────────────────
  const writeFile = createTool({
    id: "workspace_write_file",
    description:
      `Write or create a file in the local workspace (root: ${root}). ` +
      "Replaces the entire file content. For partial edits use the bash tool with sed/awk.",
    inputSchema: z.object({
      path: z.string().describe("Relative path of the file to write."),
      content: z.string().describe("Full content to write to the file."),
      createDirs: z
        .boolean()
        .default(true)
        .describe("Create parent directories if they don't exist."),
    }),
    execute: async ({ path: userPath, content, createDirs }) => {
      try {
        const resolvedPath = safeResolve(userPath);

        if (createDirs) {
          await fs.mkdir(path.dirname(resolvedPath), { recursive: true });
        }

        await fs.writeFile(resolvedPath, content, "utf8");
        const stat = await fs.stat(resolvedPath);

        return {
          success: true,
          path: userPath,
          sizeBytes: stat.size,
          output: `File written: ${userPath} (${stat.size} bytes)`,
        };
      } catch (err) {
        return { success: false, output: `Error writing file: ${err?.message}` };
      }
    },
  });

  // ── workspace_grep ───────────────────────────────────────────────────────
  const grepFiles = createTool({
    id: "workspace_grep",
    description:
      `Search file contents in the local workspace (root: ${root}) using regex. ` +
      "Returns matching lines with file path and line number. " +
      "For simple searches, the bash tool's grep command is faster.",
    inputSchema: z.object({
      pattern: z.string().describe("Regex pattern to search for."),
      path: z.string().default(".").describe("Directory or file to search within."),
      caseInsensitive: z.boolean().default(false).describe("Case-insensitive search."),
      filePattern: z.string().optional().describe("File extension filter, e.g. '.sv', '.js'."),
    }),
    execute: async ({ pattern, path: userPath, caseInsensitive, filePattern }) => {
      try {
        const resolvedPath = safeResolve(userPath || ".");
        const regex = new RegExp(pattern, caseInsensitive ? "gi" : "g");
        const matches = [];

        async function searchDir(dirPath) {
          const entries = await fs.readdir(dirPath, { withFileTypes: true });
          for (const entry of entries) {
            if (entry.name.startsWith(".") || entry.name === "node_modules") continue;
            const fullPath = path.join(dirPath, entry.name);
            if (entry.isDirectory()) {
              await searchDir(fullPath);
            } else if (entry.isFile()) {
              if (filePattern && !entry.name.endsWith(filePattern)) continue;
              try {
                const content = await fs.readFile(fullPath, "utf8");
                const lines = content.split("\n");
                lines.forEach((line, idx) => {
                  if (regex.test(line)) {
                    matches.push({
                      file: path.relative(root, fullPath),
                      line: idx + 1,
                      content: line.trim(),
                    });
                  }
                  regex.lastIndex = 0; // reset for global regex
                });
              } catch {
                // Skip binary/unreadable files
              }
            }
          }
        }

        const stat = await fs.stat(resolvedPath).catch(() => null);
        if (!stat) return { success: false, output: `Path '${userPath}' not found.` };

        if (stat.isFile()) {
          await searchDir(path.dirname(resolvedPath));
        } else {
          await searchDir(resolvedPath);
        }

        const sample = matches.slice(0, 200);
        return {
          success: true,
          matchCount: matches.length,
          shown: sample.length,
          matches: sample,
          output:
            sample.length === 0
              ? `No matches for '${pattern}'`
              : sample.map((m) => `${m.file}:${m.line}: ${m.content}`).join("\n"),
        };
      } catch (err) {
        return { success: false, output: `Error in grep: ${err?.message}` };
      }
    },
  });

  return { listFiles, readFile, writeFile, grepFiles };
}

module.exports = {
  createWorkspaceFilesystemTools,
};
