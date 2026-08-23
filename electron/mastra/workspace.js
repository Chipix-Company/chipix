/**
 * workspace.js — SIMPLIFIED
 * =========================
 * The @mastra/core/workspace Workspace class was previously instantiated here
 * but provided no actual tools to the agent (getToolsConfig() doesn't exist,
 * and filesystem tooling wasn't wired correctly).
 *
 * LOCAL FILESYSTEM ACCESS is now handled by:
 *   - workspaceBashTool.js  (just-bash + ReadWriteFs)
 *   - workspaceFilesystemTools.js (Node.js fs directly)
 *
 * This module is kept for future Mastra Workspace integration if the API stabilizes.
 */

const path = require("node:path");

function resolveWorkspaceRoot(hint) {
  const raw = hint
    || process.env.CHIPVERIFY_WORKSPACE_ROOT
    || process.cwd();
  return path.resolve(raw);
}

/**
 * Returns workspace config (no longer creates a Mastra Workspace object).
 * The workspace root is used by bash and fs tools.
 */
function createWorkspace({ workspaceRoot } = {}) {
  const root = resolveWorkspaceRoot(workspaceRoot);
  console.info("[Mastra Workspace] Workspace root resolved:", root);
  return { root, type: "local" };
}

module.exports = {
  createWorkspace,
  resolveWorkspaceRoot,
};
