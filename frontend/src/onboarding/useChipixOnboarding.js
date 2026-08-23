import { useCallback, useEffect, useRef } from "react";
import { EVENTS, STATUS, useJoyride } from "react-joyride";
import { AnalyticsEvents, track } from "../lib/observability";
import { buildJoyrideOptions, buildJoyrideStyles } from "./joyrideTheme";
import { CHIPIX_SHELL_STEPS } from "./tours/chipixShell";
import {
  clearShellTourProgress,
  markShellTourCompleted,
  markShellTourSkipped,
  shouldAutoStartShellTour,
} from "./storage";
import { TOUR_IDS } from "./constants";

const AUTO_START_DELAY_MS = 900;

/**
 * Guided shell tour for ThreadFirstWorkspace.
 *
 * @param {object} opts
 * @param {boolean} opts.enabled — false when no project / workspace hidden
 * @param {boolean} opts.isDarkTheme
 * @param {string} [opts.projectId]
 */
export default function useChipixOnboarding({ enabled = false, isDarkTheme = true, projectId = "" }) {
  const autoStartedRef = useRef(false);
  const tourId = TOUR_IDS.SHELL;

  const handleEvent = useCallback((data) => {
    const { type, index, status, step } = data;

    if (type === EVENTS.TOOLTIP) {
      track(AnalyticsEvents.ONBOARDING_STEP_VIEWED, {
        tour_id: tourId,
        step_index: index,
        step_target: typeof step?.target === "string" ? step.target : "custom",
        project_id: projectId || undefined,
      });
    }

    if (status === STATUS.FINISHED) {
      markShellTourCompleted();
      track(AnalyticsEvents.ONBOARDING_TOUR_COMPLETED, {
        tour_id: tourId,
        project_id: projectId || undefined,
      });
    }

    if (status === STATUS.SKIPPED) {
      markShellTourSkipped();
      track(AnalyticsEvents.ONBOARDING_TOUR_SKIPPED, {
        tour_id: tourId,
        step_index: index,
        project_id: projectId || undefined,
      });
    }
  }, [projectId, tourId]);

  const { controls, Tour } = useJoyride({
    continuous: true,
    steps: CHIPIX_SHELL_STEPS,
    options: {
      ...buildJoyrideOptions(isDarkTheme),
      scrollDuration: 350,
      scrollOffset: 72,
    },
    styles: buildJoyrideStyles(isDarkTheme),
    onEvent: handleEvent,
    scrollToFirstStep: true,
  });

  const startTour = useCallback(
    (opts = {}) => {
      const { force = false } = opts;
      if (!enabled) return;
      if (!force && !shouldAutoStartShellTour()) return;

      track(AnalyticsEvents.ONBOARDING_TOUR_STARTED, {
        tour_id: tourId,
        trigger: force ? "manual" : "auto",
        project_id: projectId || undefined,
      });

      controls.start(0);
    },
    [controls, enabled, projectId, tourId],
  );

  const restartTour = useCallback(() => {
    if (!enabled) return;
    clearShellTourProgress();
    autoStartedRef.current = true;
    startTour({ force: true });
  }, [enabled, startTour]);

  useEffect(() => {
    autoStartedRef.current = false;
  }, [projectId]);

  useEffect(() => {
    if (!enabled || autoStartedRef.current) return undefined;
    if (!shouldAutoStartShellTour()) return undefined;

    const timer = window.setTimeout(() => {
      autoStartedRef.current = true;
      startTour({ force: false });
    }, AUTO_START_DELAY_MS);

    return () => window.clearTimeout(timer);
  }, [enabled, projectId, startTour]);

  return { Tour, startTour, restartTour };
}
