/**
 * workspaceBashTool.js
 * ====================
 * A `bash` Mastra tool powered by just-bash + ReadWriteFs.
 *
 * This gives the agent a REAL shell on the user's workspace directory:
 *  - ls, find, cat        → reads real files from disk
 *  - echo "..." > file    → writes real files to disk
 *  - grep, awk, sed, diff → standard text processing
 *  - mkdir, cp, mv        → directory/file management
 *
 * The workspace root defaults to process.cwd() (project root in dev mode)
 * and can be overridden via CHIPVERIFY_WORKSPACE_ROOT env var or at call-time.
 *
 * Security:
 *  - ReadWriteFs scopes all operations inside workspaceRoot
 *  - Delete operations require a separate approval gate in the agent instructions
 *  - Network access is disabled by default
 */

const { z } = require("zod");
const path = require("node:path");

async function loadCreateTool() {
  const mod = await import("@mastra/core/tools");
  return mod.createTool || mod.default?.createTool || mod.default;
}

// ── Constants ──────────────────────────────────────────────────────────────
const MAX_OUTPUT_CHARS = 24_000; // truncate huge outputs to avoid context window bloat
const COMMAND_TIMEOUT_MS = 30_000; // 30 second hard timeout per command

// ── Bash instance cache keyed by workspace root ────────────────────────────
const bashCache = new Map();

function ensureWorkspaceRootExists(workspaceRoot) {
  const resolvedRoot = path.resolve(workspaceRoot);
  const fs = require("node:fs");

  if (!fs.existsSync(resolvedRoot)) {
    fs.mkdirSync(resolvedRoot, { recursive: true });
  }

  return resolvedRoot;
}

function getOrCreateBash(workspaceRoot) {
  const resolvedRoot = ensureWorkspaceRootExists(workspaceRoot);

  if (bashCache.has(resolvedRoot)) {
    return bashCache.get(resolvedRoot);
  }

  const { Bash, ReadWriteFs } = require("just-bash");

  const rwfs = new ReadWriteFs({ root: resolvedRoot });
  const bash = new Bash({
    fs: rwfs,
    cwd: "/",          // "/" in the virtual FS = resolvedRoot on disk
    executionLimits: {
      maxCommandCount: 2000,
      maxLoopIterations: 5000,
    },
  });

  bashCache.set(resolvedRoot, { bash, root: resolvedRoot });
  console.info(`[Bash Tool] Created bash instance for workspace: ${resolvedRoot}`);
  return { bash, root: resolvedRoot };
}

// ── Sanitize command output ────────────────────────────────────────────────
function sanitizeOutput(text, maxChars = MAX_OUTPUT_CHARS) {
  if (!text) return "";
  const str = String(text);
  if (str.length <= maxChars) return str;
  const half = Math.floor(maxChars / 2);
  return `${str.slice(0, half)}\n... [truncated ${str.length - maxChars} chars] ...\n${str.slice(-half)}`;
}

// ── Tool factory ───────────────────────────────────────────────────────────
async function createWorkspaceBashTool({ workspaceRoot } = {}) {
  const createTool = await loadCreateTool();
  const root = workspaceRoot
    || process.env.CHIPVERIFY_WORKSPACE_ROOT
    || process.cwd();

  const resolvedRoot = ensureWorkspaceRootExists(root);

  // Pre-warm the bash instance
  try {
    getOrCreateBash(resolvedRoot);
  } catch (err) {
    console.warn("[Bash Tool] Pre-warm failed:", err?.message);
  }

  return createTool({
    id: "bash",

    description: [
      `Execute bash commands directly in the workspace at: ${resolvedRoot}`,
      "",
      "Use this tool for ALL local filesystem operations:",
      "  - List files:    ls -la, ls -R, find . -name '*.sv'",
      "  - Read files:    cat README.md, head -50 src/main.js",
      "  - Search:        grep -rn 'TODO' ., find . -newer package.json",
      "  - Create files:  echo 'content' > file.txt, tee file.txt <<'EOF'...EOF",
      "  - Edit files:    Use cat with heredoc or sed for in-place edits",
      "  - Count:         ls | wc -l, find . -name '*.sv' | wc -l",
      "  - Stats:         du -sh ., wc -l **/*.js",
      "",
      "Return the raw command output as-is. Do not summarize prematurely.",
      "If a command fails, show the error and suggest a corrected command.",
    ].join("\n"),

    inputSchema: z.object({
      command: z
        .string()
        .describe(
          "The bash command or multi-line script to execute. " +
          "Use standard unix syntax. Working directory is the workspace root. " +
          "Examples: 'ls -la', 'cat package.json', 'find . -name \"*.sv\" -type f'",
        ),
    }),

    execute: async ({ command }) => {
      const trimmedCmd = String(command || "").trim();
      if (!trimmedCmd) {
        return { success: false, output: "Error: empty command", exitCode: 1 };
      }

      let bashInstance;
      try {
        bashInstance = getOrCreateBash(resolvedRoot);
      } catch (err) {
        return {
          success: false,
          output: `Error: Failed to initialize bash environment: ${err?.message}`,
          exitCode: 1,
        };
      }

      const { bash } = bashInstance;

      try {
        // Run with timeout via AbortController
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), COMMAND_TIMEOUT_MS);

        let result;
        try {
          result = await bash.exec(trimmedCmd, { signal: controller.signal });
        } finally {
          clearTimeout(timeout);
        }

        const stdout = sanitizeOutput(result.stdout || "");
        const stderr = sanitizeOutput(result.stderr || "");
        const success = result.exitCode === 0;

        // Build human-readable output
        const parts = [];
        if (stdout) parts.push(stdout);
        if (stderr) {
          // Only surface stderr prominently on failure
          if (!success) {
            parts.push(`\nSTDERR:\n${stderr}`);
          } else if (stderr.trim()) {
            parts.push(`\n(stderr: ${stderr.trim()})`);
          }
        }

        const output = parts.join("").trim() || (success ? "(command succeeded with no output)" : "(command failed with no output)");

        return {
          success,
          output,
          exitCode: result.exitCode,
          workspaceRoot: resolvedRoot,
        };
      } catch (err) {
        if (err?.name === "AbortError") {
          return {
            success: false,
            output: `Error: Command timed out after ${COMMAND_TIMEOUT_MS / 1000}s: ${trimmedCmd}`,
            exitCode: 124,
          };
        }
        return {
          success: false,
          output: `Error: Bash execution failed: ${err?.message || String(err)}`,
          exitCode: 1,
        };
      }
    },
  });
}

// ── Invalidate cache when workspace root changes ──────────────────────────
function invalidateBashCache(workspaceRoot) {
  if (workspaceRoot) {
    bashCache.delete(path.resolve(workspaceRoot));
  } else {
    bashCache.clear();
  }
}

module.exports = {
  createWorkspaceBashTool,
  invalidateBashCache,
};
