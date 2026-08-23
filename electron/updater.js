const { app, ipcMain, shell, BrowserWindow } = require("electron");
const log = require("electron-log");
const {
  captureElectronException,
  captureElectronMessage,
} = require("./observability/sentry");

function getAutoUpdater() {
  return require("electron-updater").autoUpdater;
}

const VERSION_ENDPOINT = "/api/desktop/version";
const DEFAULT_CHECK_INTERVAL_MS = 5 * 60 * 1000;
const MIN_CHECK_INTERVAL_MS = 30 * 1000;

let lastRemoteInfo = null;
let periodicTimer = null;
let checkInFlight = false;

function readPackagedConvexSiteUrl() {
  try {
    const pkg = require("../package.json");
    const configured = pkg?.chipverify?.convexSiteUrl;
    if (typeof configured === "string" && configured.trim()) {
      return configured.trim().replace(/\/+$/, "");
    }
  } catch {
    // Ignore missing package metadata in unusual launch contexts.
  }
  return "https://next-swordfish-62.convex.site";
}

let updateState = {
  status: "idle",
  currentVersion: app.getVersion(),
  remoteVersion: null,
  releaseNotes: "",
  downloadProgress: null,
  error: null,
  forceUpdate: false,
  updateAvailable: false,
  updateDownloaded: false,
  lastCheckedAt: null,
  upToDate: false,
  manualDownloadAvailable: false,
  downloadUrl: null,
};

function getConvexSiteUrl() {
  return (
    process.env.CHIPVERIFY_CONVEX_SITE_URL
    || process.env.CONVEX_SITE_URL
    || readPackagedConvexSiteUrl()
  ).replace(/\/+$/, "");
}

function getCheckIntervalMs() {
  const raw = Number.parseInt(process.env.CHIPVERIFY_UPDATE_CHECK_INTERVAL_MS || "", 10);
  if (Number.isFinite(raw) && raw >= MIN_CHECK_INTERVAL_MS) {
    return raw;
  }
  return DEFAULT_CHECK_INTERVAL_MS;
}

function parseVersionParts(version) {
  const cleaned = String(version || "0.0.0").trim().replace(/^v/i, "");
  const [core = "0.0.0", prerelease = ""] = cleaned.split("-");
  const segments = core.split(".").map((part) => Number.parseInt(part, 10) || 0);
  while (segments.length < 3) {
    segments.push(0);
  }
  return { segments, prerelease };
}

function compareVersions(left, right) {
  const a = parseVersionParts(left);
  const b = parseVersionParts(right);

  for (let index = 0; index < 3; index += 1) {
    if (a.segments[index] > b.segments[index]) return 1;
    if (a.segments[index] < b.segments[index]) return -1;
  }

  if (!a.prerelease && b.prerelease) return 1;
  if (a.prerelease && !b.prerelease) return -1;
  if (a.prerelease === b.prerelease) return 0;
  return a.prerelease > b.prerelease ? 1 : -1;
}

function isRemoteVersionNewer(remoteVersion, localVersion) {
  return compareVersions(remoteVersion, localVersion) > 0;
}

function isBelowMinimum(localVersion, minSupportedVersion) {
  if (!minSupportedVersion) return false;
  return compareVersions(localVersion, minSupportedVersion) < 0;
}

function broadcastUpdateEvent(mainWindow, payload) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }
  mainWindow.webContents.send("chipverify:update:event", payload);
}

function setUpdateState(patch, mainWindow) {
  updateState = { ...updateState, ...patch };
  broadcastUpdateEvent(mainWindow, { type: "state", state: updateState });
}

async function fetchRemoteReleaseInfo(platform) {
  const endpoint = `${getConvexSiteUrl()}${VERSION_ENDPOINT}?platform=${encodeURIComponent(platform)}`;
  const response = await fetch(endpoint, {
    method: "GET",
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new Error(`Convex version check failed (${response.status})`);
  }

  return response.json();
}

function configureAutoUpdater(getMainWindow) {
  const autoUpdater = getAutoUpdater();
  autoUpdater.logger = log;
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.on("checking-for-update", () => {
    setUpdateState({ status: "checking", error: null }, getMainWindow());
  });

  autoUpdater.on("update-available", (info) => {
    setUpdateState({
      status: "downloading",
      updateAvailable: true,
      remoteVersion: info?.version || updateState.remoteVersion,
      releaseNotes: typeof info?.releaseNotes === "string" ? info.releaseNotes : updateState.releaseNotes,
      error: null,
      upToDate: false,
    }, getMainWindow());
  });

  autoUpdater.on("update-not-available", () => {
    setUpdateState({
      status: "idle",
      updateAvailable: false,
      updateDownloaded: false,
      error: null,
    }, getMainWindow());
  });

  autoUpdater.on("download-progress", (progress) => {
    setUpdateState({
      status: "downloading",
      downloadProgress: {
        percent: progress?.percent ?? 0,
        transferred: progress?.transferred ?? 0,
        total: progress?.total ?? 0,
        bytesPerSecond: progress?.bytesPerSecond ?? 0,
      },
    }, getMainWindow());
  });

  autoUpdater.on("update-downloaded", (info) => {
    setUpdateState({
      status: "ready",
      updateDownloaded: true,
      remoteVersion: info?.version || updateState.remoteVersion,
      downloadProgress: null,
      error: null,
      upToDate: false,
    }, getMainWindow());
  });

  autoUpdater.on("error", (error) => {
    const message = error?.message || "Auto-update failed.";
    log.error("[AutoUpdate]", message);
    captureElectronException(error instanceof Error ? error : new Error(message), {
      area: "auto_update",
      status: updateState.status,
      remoteVersion: updateState.remoteVersion,
    });

    const hint = /404|403|not found|bad credentials/i.test(message)
      ? " Release files may be on a private GitHub repo. Publish the update feed/installer to Convex or use a public download URL."
      : "";

    setUpdateState({
      status: "error",
      error: `${message}${hint}`,
      manualDownloadAvailable: Boolean(lastRemoteInfo?.downloadUrl),
      downloadUrl: lastRemoteInfo?.downloadUrl || updateState.downloadUrl,
    }, getMainWindow());
  });
}

async function applyRemoteFeedUrl(remoteInfo) {
  const feedUrl = String(remoteInfo?.updateFeedUrl || "").trim();
  if (!feedUrl) {
    return false;
  }

  const normalizedFeedUrl = feedUrl
    .replace(/\/latest(?:-[a-z0-9_-]+)?\.ya?ml$/i, "")
    .replace(/\/+$/, "");

  getAutoUpdater().setFeedURL({
    provider: "generic",
    url: normalizedFeedUrl,
  });
  return true;
}

async function openDownloadFallback(remoteInfo) {
  const downloadUrl = String(remoteInfo?.downloadUrl || "").trim();
  if (!downloadUrl) {
    return false;
  }
  await shell.openExternal(downloadUrl);
  return true;
}

async function checkForUpdates(mainWindow, { manual = false } = {}) {
  if (!app.isPackaged && !manual) {
    return { ...updateState, skipped: true, reason: "dev-mode" };
  }

  if (checkInFlight) {
    return updateState;
  }

  checkInFlight = true;
  setUpdateState({
    status: "checking-remote",
    error: null,
    upToDate: false,
    lastCheckedAt: Date.now(),
  }, mainWindow);

  try {
    const remoteInfo = await fetchRemoteReleaseInfo(process.platform);
    lastRemoteInfo = remoteInfo;
    const localVersion = app.getVersion();
    const remoteVersion = remoteInfo?.version || localVersion;
    const minSupportedVersion = remoteInfo?.minSupportedVersion || localVersion;
    const forceUpdate = Boolean(remoteInfo?.forceUpdate) || isBelowMinimum(localVersion, minSupportedVersion);
    const updateAvailable = isRemoteVersionNewer(remoteVersion, localVersion) || forceUpdate;

    setUpdateState({
      remoteVersion,
      releaseNotes: remoteInfo?.releaseNotes || "",
      forceUpdate,
      updateAvailable,
      currentVersion: localVersion,
      downloadUrl: remoteInfo?.downloadUrl || null,
      manualDownloadAvailable: Boolean(remoteInfo?.downloadUrl),
    }, mainWindow);

    if (!updateAvailable) {
      setUpdateState({
        status: "idle",
        upToDate: manual,
        updateDownloaded: false,
      }, mainWindow);
      return updateState;
    }

    const feedConfigured = await applyRemoteFeedUrl(remoteInfo);
    if (!feedConfigured) {
      const opened = await openDownloadFallback(remoteInfo);
      if (opened) {
        setUpdateState({
          status: "manual-download",
          error: null,
        }, mainWindow);
        return updateState;
      }

      throw new Error(
        "Update available but no updateFeedUrl or downloadUrl is configured in Convex.",
      );
    }

    await getAutoUpdater().checkForUpdates();
    return updateState;
  } catch (error) {
    const message = error?.message || "Unable to check for updates.";
    log.error("[AutoUpdate] check failed", message);
    captureElectronMessage(message, "error", {
      area: "auto_update_check",
      convexSiteUrl: getConvexSiteUrl(),
    });
    setUpdateState({
      status: "error",
      error: message,
      manualDownloadAvailable: Boolean(lastRemoteInfo?.downloadUrl),
      downloadUrl: lastRemoteInfo?.downloadUrl || updateState.downloadUrl,
    }, mainWindow);
    return updateState;
  } finally {
    checkInFlight = false;
  }
}

function registerAutoUpdateIpc(getMainWindow) {
  ipcMain.handle("chipverify:update:status", () => ({
    ...updateState,
    currentVersion: app.getVersion(),
    convexSiteUrl: getConvexSiteUrl(),
    isPackaged: app.isPackaged,
  }));

  ipcMain.handle("chipverify:update:check", async () => {
    return checkForUpdates(getMainWindow(), { manual: true });
  });

  ipcMain.handle("chipverify:update:install", () => {
    if (!updateState.updateDownloaded) {
      return { ok: false, reason: "no-downloaded-update" };
    }
    getAutoUpdater().quitAndInstall(false, true);
    return { ok: true };
  });

  ipcMain.handle("chipverify:update:open-download", async () => {
    if (!lastRemoteInfo?.downloadUrl && !updateState.downloadUrl) {
      return { ok: false, reason: "no-download-url" };
    }
    await openDownloadFallback(lastRemoteInfo || { downloadUrl: updateState.downloadUrl });
    return { ok: true };
  });

  ipcMain.handle("chipverify:app:version", () => app.getVersion());
}

function startPeriodicUpdateChecks(getMainWindow) {
  if (!app.isPackaged) {
    return;
  }

  const intervalMs = getCheckIntervalMs();
  if (periodicTimer) {
    clearInterval(periodicTimer);
  }

  periodicTimer = setInterval(() => {
    if (updateState.status === "downloading" || updateState.status === "ready") {
      return;
    }
    void checkForUpdates(getMainWindow());
  }, intervalMs);

  app.on("browser-window-focus", () => {
    if (updateState.status === "downloading" || updateState.status === "ready") {
      return;
    }
    const elapsed = Date.now() - (updateState.lastCheckedAt || 0);
    if (elapsed >= MIN_CHECK_INTERVAL_MS) {
      void checkForUpdates(getMainWindow());
    }
  });
}

function startAutoUpdateOnLaunch(getMainWindow) {
  configureAutoUpdater(getMainWindow);
  registerAutoUpdateIpc(getMainWindow);

  if (!app.isPackaged) {
    log.info("[AutoUpdate] Skipping startup check in unpackaged dev mode.");
    return;
  }

  const runStartupCheck = () => {
    void checkForUpdates(getMainWindow());
    startPeriodicUpdateChecks(getMainWindow);
  };

  if (getMainWindow()) {
    runStartupCheck();
  } else {
    app.once("chipverify:main-window-ready", runStartupCheck);
  }
}

function scheduleAutoUpdateCheck(getMainWindow) {
  if (!app.isPackaged) {
    return;
  }
  void checkForUpdates(getMainWindow(), { manual: true });
}

module.exports = {
  startAutoUpdateOnLaunch,
  scheduleAutoUpdateCheck,
  checkForUpdates,
  compareVersions,
  getConvexSiteUrl,
};
