const { contextBridge, ipcRenderer } = require("electron");

const DEFAULT_BACKEND_URL = "http://127.0.0.1:7348";

function normalizeBackendBaseUrl(rawUrl) {
  if (typeof rawUrl !== "string") {
    return "";
  }

  const trimmed = rawUrl.trim();
  if (!trimmed) {
    return "";
  }

  const withoutApiPath = trimmed.replace(/\/+$/, "").replace(/\/api\/v1$/i, "");

  try {
    const parsed = new URL(withoutApiPath);
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString().replace(/\/+$/, "");
  } catch {
    return withoutApiPath;
  }
}

const managedBackendUrl =
  normalizeBackendBaseUrl(process.env.CHIPVERIFY_BACKEND_URL || "") || null;
const backendUrl = managedBackendUrl || DEFAULT_BACKEND_URL;

const capabilities = {
  startCopilotChat: typeof ipcRenderer.invoke === "function",
  stopCopilotChat: typeof ipcRenderer.invoke === "function",
  approveCopilotTool: typeof ipcRenderer.invoke === "function",
  declineCopilotTool: typeof ipcRenderer.invoke === "function",
  runWorkspaceCommand: typeof ipcRenderer.invoke === "function",
  checkCopilotHealth: typeof ipcRenderer.invoke === "function",
  onCopilotEvent: typeof ipcRenderer.on === "function" && typeof ipcRenderer.removeListener === "function",
  pickFile: typeof ipcRenderer.invoke === "function",
  pickFiles: typeof ipcRenderer.invoke === "function",
  pickDirectory: typeof ipcRenderer.invoke === "function",
  getBackendUrl: typeof ipcRenderer.invoke === "function",
  getBackendDiagnostics: typeof ipcRenderer.invoke === "function",
  getActivationStatus: typeof ipcRenderer.invoke === "function",
  activateMachine: typeof ipcRenderer.invoke === "function",
  windowControls: typeof ipcRenderer.invoke === "function",
  getAppVersion: typeof ipcRenderer.invoke === "function",
  getUpdateStatus: typeof ipcRenderer.invoke === "function",
  checkForUpdates: typeof ipcRenderer.invoke === "function",
  installUpdate: typeof ipcRenderer.invoke === "function",
  openUpdateDownload: typeof ipcRenderer.invoke === "function",
  onUpdateEvent: typeof ipcRenderer.on === "function" && typeof ipcRenderer.removeListener === "function",
};

const bridgeMeta = {
  name: "chipverify-electron-bridge",
  version: 1,
  loadedAt: Date.now(),
  capabilities,
};

const electronAPI = {
  platform: process.platform,
  backendUrl,
  managedBackendUrl,
  window: {
    minimize: () => ipcRenderer.invoke("chipverify:window:minimize"),
    toggleMaximize: () => ipcRenderer.invoke("chipverify:window:toggle-maximize"),
    close: () => ipcRenderer.invoke("chipverify:window:close"),
    isMaximized: () => ipcRenderer.invoke("chipverify:window:is-maximized"),
    onMaximizeState: (listener) => {
      if (typeof listener !== "function") return () => {};
      const wrapped = (_event, isMax) => listener(Boolean(isMax));
      ipcRenderer.on("chipverify:window:maximize-state", wrapped);
      return () => ipcRenderer.removeListener("chipverify:window:maximize-state", wrapped);
    },
  },
  getBackendUrl: () => ipcRenderer.invoke("chipverify:get-backend-url"),
  getBackendDiagnostics: () => ipcRenderer.invoke("chipverify:backend:diagnostics"),
  getWorkspaceRoot: () => ipcRenderer.invoke("chipverify:get-workspace-root"),
  getActivationStatus: () => ipcRenderer.invoke("chipverify:activation:status"),
  activateMachine: (payload) => ipcRenderer.invoke("chipverify:activation:activate", payload),
  getAppVersion: () => ipcRenderer.invoke("chipverify:app:version"),
  getUpdateStatus: () => ipcRenderer.invoke("chipverify:update:status"),
  checkForUpdates: () => ipcRenderer.invoke("chipverify:update:check"),
  installUpdate: () => ipcRenderer.invoke("chipverify:update:install"),
  openUpdateDownload: () => ipcRenderer.invoke("chipverify:update:open-download"),
  onUpdateEvent: (listener) => {
    if (typeof listener !== "function") {
      return () => {};
    }

    const wrapped = (_event, payload) => listener(payload);
    ipcRenderer.on("chipverify:update:event", wrapped);
    return () => ipcRenderer.removeListener("chipverify:update:event", wrapped);
  },
  pickFile: (options) => ipcRenderer.invoke("chipverify:pick-file", options),
  pickFiles: (options) => ipcRenderer.invoke("chipverify:pick-files", options),
  pickDirectory: (options) => ipcRenderer.invoke("chipverify:pick-directory", options),
  startCopilotChat: (payload) => ipcRenderer.invoke("chipverify:copilot:start", payload),
  stopCopilotChat: (payload) => ipcRenderer.invoke("chipverify:copilot:stop", payload),
  approveCopilotTool: (payload) => ipcRenderer.invoke("chipverify:copilot:approve-tool", payload),
  declineCopilotTool: (payload) => ipcRenderer.invoke("chipverify:copilot:decline-tool", payload),
  runWorkspaceCommand: (payload) => ipcRenderer.invoke("chipverify:workspace:bash", payload),
  checkCopilotHealth: () => ipcRenderer.invoke("chipverify:copilot:health"),
  onCopilotEvent: (listener) => {
    if (typeof listener !== "function") {
      return () => {};
    }

    const wrapped = (_event, payload) => listener(payload);
    ipcRenderer.on("chipverify:copilot:event", wrapped);

    return () => {
      ipcRenderer.removeListener("chipverify:copilot:event", wrapped);
    };
  },
  bridgeMeta,
  getBridgeStatus: () => ({
    ready: true,
    loadedAt: bridgeMeta.loadedAt,
    capabilities,
  }),
};

try {
  contextBridge.exposeInMainWorld("electronAPI", electronAPI);
} catch (error) {
  console.error("[Electron preload] Failed to expose electronAPI", error);
}
