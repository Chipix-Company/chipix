export function deriveUpdateUiState(status = {}) {
  const blocked = Boolean(
    status?.forceUpdate && status?.updateAvailable && status?.status !== "ready",
  );
  const checking = ["checking", "checking-remote"].includes(status?.status);
  const downloading = status?.status === "downloading";
  const ready = status?.status === "ready" && status?.updateDownloaded;
  const upToDate = Boolean(status?.upToDate);
  const showModal = Boolean(
    blocked
    || (status?.updateAvailable && (checking || downloading || ready))
    || (status?.status === "error" && status?.updateAvailable),
  );
  return {
    blocked,
    checking,
    downloading,
    ready,
    upToDate,
    showModal,
  };
}

export function formatLastChecked(lastCheckedAt, { relative = false } = {}) {
  if (!lastCheckedAt) return "Not yet";
  try {
    const date = new Date(lastCheckedAt);
    if (Number.isNaN(date.getTime())) return "Unknown";
    if (!relative) return date.toLocaleString();

    const diff = Date.now() - date.getTime();
    if (diff < 60000) return "Just now";
    if (diff < 3600000) return `${Math.round(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.round(diff / 3600000)}h ago`;
    return date.toLocaleDateString();
  } catch {
    return "Unknown";
  }
}

export function getUpdateStatusSummary(status = {}, ui = {}) {
  if (status.loading || ui.checking) {
    return { tone: "checking", title: "Checking…", message: "Looking for the latest ChipVerify build." };
  }
  if (ui.ready) {
    return {
      tone: "ready",
      title: "Ready to install",
      message: `Version ${status.remoteVersion || "update"} downloaded. Restart to finish.`,
    };
  }
  if (ui.downloading) {
    return {
      tone: "downloading",
      title: "Downloading update",
      message: `Fetching version ${status.remoteVersion || ""} in the background.`,
    };
  }
  if (status.error) {
    return { tone: "error", title: "Update check failed", message: status.error };
  }
  if (status.updateAvailable && status.remoteVersion) {
    return {
      tone: "available",
      title: "Update available",
      message: `Version ${status.remoteVersion} is ready to download.`,
    };
  }
  if (ui.upToDate || (status.remoteVersion && status.currentVersion === status.remoteVersion)) {
    return { tone: "ok", title: "Up to date", message: "You're running the latest published version." };
  }
  if (status.remoteVersion) {
    return { tone: "ok", title: "Up to date", message: "You're running the latest published version." };
  }
  return { tone: "idle", title: "Check for updates", message: "We'll compare your build against the release channel." };
}
