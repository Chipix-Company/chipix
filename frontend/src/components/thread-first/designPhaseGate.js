/**
 * Thread cards for the mandatory design → mental model → verification handoff.
 */

import { THREAD_KINDS, makeItem } from "./types";

export const GATE = {
  POST_DESIGN: "post-design",
  IMPORTED: "imported",
  VERIFY: "verify",
};

/** Plain-English explainer shown before the first mental model on imported RTL. */
export const MENTAL_MODEL_EXPLAINER =
  "A mental model is Chipix's shared map of your design — what the spec says, which modules exist, how they connect, and what behavior matters. Verification strategies and tests are planned from that understanding so checks match your intent, not just syntax.";

export function makeImportedDesignOnboardingSaid() {
  return makeItem(THREAD_KINDS.SAID, {
    text: "Your spec and RTL are in the project. Next I'll read them and build a mental model — then we'll recommend verification strategies, draft a plan, and run the pipeline.",
  });
}

export function makeImportedDesignOnboardingAsk() {
  return makeItem(THREAD_KINDS.ASK, {
    _gate: GATE.IMPORTED,
    question: "Build a mental model from your files?",
    sub: MENTAL_MODEL_EXPLAINER,
    choices: [
      { id: "gate-build-mental-model", label: "Yes — build mental model" },
      { id: "gate-open-ide", label: "Review RTL in editor first" },
    ],
  });
}

export function makePostDesignGateAsk() {
  return makeItem(THREAD_KINDS.ASK, {
    _gate: GATE.POST_DESIGN,
    question: "Design phase is complete. What would you like to do next?",
    sub: "Review or edit your RTL, then build a mental model — verification tests are planned from that understanding.",
    choices: [
      { id: "gate-open-ide", label: "Open in editor" },
      { id: "gate-build-mental-model", label: "Build mental model" },
    ],
  });
}

export function makeVerificationGateAsk() {
  return makeItem(THREAD_KINDS.ASK, {
    _gate: GATE.VERIFY,
    question: "Start verification?",
    sub: "I'll recommend strategies, draft a test plan you can approve, then run the checks.",
    choices: [
      { id: "gate-start-verification", label: "Yes, plan and run verification" },
      { id: "gate-open-ide", label: "Open in editor first" },
      { id: "gate-read-rtl", label: "Let me read the RTL first" },
    ],
  });
}

export function makeVerificationUploadAsk({ hasSpec = false, hasRtl = false } = {}) {
  const missing = [
    hasSpec ? null : "a specification",
    hasRtl ? null : "RTL source",
  ].filter(Boolean);
  const question = missing.length
    ? `Upload ${missing.join(" and ")} before verification`
    : "Upload files before verification";
  const choices = [];
  if (!hasRtl) {
    choices.push(
      { id: "upload-rtl", label: "Upload RTL file(s)" },
      { id: "upload-rtl-folder", label: "Upload RTL folder" },
    );
  }
  if (!hasSpec) {
    choices.push({ id: "upload-spec", label: "Upload spec document" });
  }
  choices.push({ id: "gate-open-ide", label: "Open IDE" });

  return makeItem(THREAD_KINDS.ASK, {
    _gate: "verification-upload",
    question,
    sub: "Verification needs project files first. Attach your RTL, and ideally the spec, then I'll build TruthCore and plan the run.",
    choices,
  });
}

export function makeDesignPhaseCompleteSaid(fileCount) {
  const n = Number(fileCount) || 0;
  const files = n === 1 ? "1 file is" : `${n} files are`;
  return makeItem(THREAD_KINDS.SAID, {
    text: `Designing is finished — ${files} in your project. Next we align on how I understand the design (mental model), then we move into verification.`,
  });
}

export function makeMentalModelCheckpointSaid() {
  return makeItem(THREAD_KINDS.SAID, {
    text: "Mental model confirmed. We're done with the design-understanding step — ready for the verification phase.",
  });
}

export function makeDesignPhaseBreak() {
  return makeItem(THREAD_KINDS.BREAK, { label: "Design phase complete" });
}
