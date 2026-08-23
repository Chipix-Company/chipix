const path = require("node:path");

let _memory = null;
let _memoryPromise = null;

/**
 * Create a LibSQL-backed, project-scoped Memory instance.
 * On first call the DB file is created in Electron's userData directory
 * so it persists across app restarts.
 */
async function createProjectScopedMemory() {
  if (_memory) return _memory;
  if (_memoryPromise) return _memoryPromise;

  _memoryPromise = (async () => {
    const memoryModule = await import("@mastra/memory");
    const Memory = memoryModule.Memory || memoryModule.default?.Memory || memoryModule.default;

    let storage;
    let storageType = "in-memory";

    // LibSQL persistent storage — survives Electron restarts
    try {
      const libsqlModule = await import("@mastra/libsql");
      const LibSQLStore = libsqlModule.LibSQLStore || libsqlModule.default?.LibSQLStore || libsqlModule.default;

      // In Electron main process, app.getPath is available.
      // Fallback to cwd-relative path if running outside Electron (unit tests, etc.)
      let dbDir;
      try {
        const { app } = require("electron");
        dbDir = app.getPath("userData");
      } catch {
        dbDir = path.join(process.cwd(), ".mastra-data");
        require("node:fs").mkdirSync(dbDir, { recursive: true });
      }

      const dbPath = path.join(dbDir, "chipverify-memory.db");
      storage = new LibSQLStore({
        id: "chipverify-memory-storage",
        url: `file:${dbPath}`,
      });
      storageType = "libsql";
    } catch (err) {
      // LibSQL unavailable — fall through to in-memory (package not installed yet).
      console.warn("[Mastra Memory] LibSQL unavailable, using in-memory store:", err?.message);
    }

    const memoryOptions = {
      lastMessages: 30,
      observationalMemory: true,
      workingMemory: {
        enabled: true,
        scope: "thread",
      },
    };

    _memory = new Memory(
      storage
        ? { storage, options: memoryOptions }
        : { options: memoryOptions },
    );

    console.info(`[Mastra Memory] Initialized with ${storageType} storage`);
    return _memory;
  })();

  try {
    return await _memoryPromise;
  } finally {
    _memoryPromise = null;
  }
}

/**
 * Build the thread/resource keys for project-scoped memory.
 * Using composite keys keeps threads isolated per project.
 */
function buildMemoryScope({ projectId, threadId, userId } = {}) {
  const projectKey = String(projectId || "default-project");
  const userKey = String(userId || "anonymous");
  const threadKey = String(threadId || "default-thread");

  return {
    thread: `${projectKey}:${threadKey}`,
    resource: `${projectKey}:${userKey}`,
  };
}

module.exports = {
  createProjectScopedMemory,
  buildMemoryScope,
};
