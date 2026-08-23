import { useEffect, useRef } from "react";
import { maybeCelebrateUpdate } from "./updateConfetti";

export default function useUpdateCelebration(updateState) {
  const previousRef = useRef(null);

  useEffect(() => {
    if (!updateState || updateState.loading) return;
    maybeCelebrateUpdate(previousRef.current, updateState);
    previousRef.current = {
      status: updateState.status,
      updateDownloaded: updateState.updateDownloaded,
      upToDate: updateState.upToDate,
      updateAvailable: updateState.updateAvailable,
      currentVersion: updateState.currentVersion,
      remoteVersion: updateState.remoteVersion,
      loading: updateState.loading,
    };
  }, [
    updateState?.status,
    updateState?.updateDownloaded,
    updateState?.upToDate,
    updateState?.updateAvailable,
    updateState?.currentVersion,
    updateState?.remoteVersion,
    updateState?.loading,
  ]);
}
