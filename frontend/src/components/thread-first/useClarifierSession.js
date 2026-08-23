import { useCallback, useMemo, useState } from "react";
import {
  formatClarifierBundle,
  formatClarifierRecap,
  prepareClarifierSteps,
} from "./clarifierSession";

/**
 * State machine for the clarifier overlay — one question at a time.
 */
export default function useClarifierSession() {
  const [session, setSession] = useState(null);

  const isOpen = Boolean(session);

  const open = useCallback(({ steps = [], intro, turnId } = {}) => {
    const prepared = prepareClarifierSteps(steps);
    if (!prepared.length) return;
    setSession({
      steps: prepared,
      intro: intro ? String(intro).trim() : "",
      stepIndex: 0,
      answers: {},
      turnId: turnId || null,
    });
  }, []);

  const close = useCallback(() => setSession(null), []);

  const currentStep = session?.steps?.[session.stepIndex] ?? null;
  const totalSteps = session?.steps?.length ?? 0;
  const isLastStep = session ? session.stepIndex >= totalSteps - 1 : false;

  const currentAnswer = currentStep
    ? session.answers[currentStep.stepId] || null
    : null;

  const pickChoice = useCallback((choiceId) => {
    setSession((prev) => {
      if (!prev) return prev;
      const step = prev.steps[prev.stepIndex];
      if (!step) return prev;
      const choice = step.choices.find((c) => c.id === choiceId);
      if (!choice) return prev;
      const keepsCustom = choice.allowsText;
      return {
        ...prev,
        answers: {
          ...prev.answers,
          [step.stepId]: {
            choiceId,
            label: choice.label,
            customText: keepsCustom ? (prev.answers[step.stepId]?.customText || "") : "",
          },
        },
      };
    });
  }, []);

  const setCustomText = useCallback((text) => {
    setSession((prev) => {
      if (!prev) return prev;
      const step = prev.steps[prev.stepIndex];
      if (!step) return prev;
      const existing = prev.answers[step.stepId];
      if (!existing) return prev;
      return {
        ...prev,
        answers: {
          ...prev.answers,
          [step.stepId]: { ...existing, customText: text },
        },
      };
    });
  }, []);

  const canAdvance = useMemo(() => {
    if (!session || !currentStep) return false;
    const ans = session.answers[currentStep.stepId];
    if (!ans?.choiceId) return false;
    const choice = currentStep.choices.find((c) => c.id === ans.choiceId);
    if (!choice) return false;
    if (choice.allowsText) {
      return Boolean((ans.customText || "").trim());
    }
    return true;
  }, [session, currentStep]);

  const goBack = useCallback(() => {
    setSession((prev) => {
      if (!prev || prev.stepIndex <= 0) return prev;
      return { ...prev, stepIndex: prev.stepIndex - 1 };
    });
  }, []);

  const goNext = useCallback(() => {
    setSession((prev) => {
      if (!prev) return prev;
      if (prev.stepIndex >= prev.steps.length - 1) return prev;
      return { ...prev, stepIndex: prev.stepIndex + 1 };
    });
  }, []);

  const buildCompletion = useCallback(() => {
    if (!session) return { message: "", recap: null };
    const message = formatClarifierBundle(session.steps, session.answers);
    const recap = formatClarifierRecap(session.steps, session.answers);
    return { message, recap, turnId: session.turnId };
  }, [session]);

  return {
    session,
    isOpen,
    open,
    close,
    currentStep,
    currentAnswer,
    totalSteps,
    stepIndex: session?.stepIndex ?? 0,
    intro: session?.intro ?? "",
    isLastStep,
    canAdvance,
    pickChoice,
    setCustomText,
    goBack,
    goNext,
    buildCompletion,
  };
}
