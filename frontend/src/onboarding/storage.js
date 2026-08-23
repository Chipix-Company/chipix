import { tourStorageKey, TOUR_IDS } from "./constants";

function readRecord(tourId) {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(tourStorageKey(tourId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function writeRecord(tourId, patch) {
  if (typeof window === "undefined") return;
  try {
    const prev = readRecord(tourId) || {};
    window.localStorage.setItem(
      tourStorageKey(tourId),
      JSON.stringify({ ...prev, ...patch, updatedAt: new Date().toISOString() }),
    );
  } catch {
    // quota / private mode
  }
}

export function isShellTourDone() {
  const r = readRecord(TOUR_IDS.SHELL);
  return Boolean(r?.completedAt || r?.skippedAt);
}

export function shouldAutoStartShellTour() {
  return !isShellTourDone();
}

export function markShellTourCompleted() {
  writeRecord(TOUR_IDS.SHELL, { completedAt: new Date().toISOString(), skippedAt: null });
}

export function markShellTourSkipped() {
  writeRecord(TOUR_IDS.SHELL, { skippedAt: new Date().toISOString() });
}

export function clearShellTourProgress() {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(tourStorageKey(TOUR_IDS.SHELL));
  } catch {
    // ignore
  }
}
