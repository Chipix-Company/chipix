export const ONBOARDING_STORAGE_PREFIX = "chipverify.onboarding";

export const TOUR_IDS = {
  SHELL: "shell",
};

export const TOUR_VERSION = {
  [TOUR_IDS.SHELL]: 1,
};

export function tourStorageKey(tourId) {
  const version = TOUR_VERSION[tourId] ?? 1;
  return `${ONBOARDING_STORAGE_PREFIX}.${tourId}.v${version}`;
}
