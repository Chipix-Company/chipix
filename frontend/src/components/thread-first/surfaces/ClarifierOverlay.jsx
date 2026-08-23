import React, { useEffect, useMemo, useRef } from "react";
import Icon from "../icons";
import TfMarkdown from "../TfMarkdown";
import { dedupeIntroHeadline, parseClarifierQuestion } from "../clarifierSession";

/**
 * ClarifierOverlay — step-by-step clarifying questions (System B).
 * One question per screen; choices + optional free text; Next / Back / Done.
 */
export default function ClarifierOverlay({
  open,
  intro,
  stepIndex = 0,
  totalSteps = 0,
  step,
  answer,
  canAdvance = false,
  isLastStep = false,
  onPickChoice,
  onCustomText,
  onBack,
  onNext,
  onComplete,
  onDismiss,
}) {
  const otherInputRef = useRef(null);
  const selectedChoice = step?.choices?.find((c) => c.id === answer?.choiceId);
  const showOtherInput = Boolean(selectedChoice?.allowsText);

  const { contextMarkdown, headline, subMarkdown } = useMemo(() => {
    const parsed = parseClarifierQuestion(step?.question);
    const deduped = dedupeIntroHeadline(intro, parsed.headline);
    const contextParts = [
      stepIndex === 0 ? deduped.intro : "",
      parsed.body,
    ].filter(Boolean);
    return {
      contextMarkdown: contextParts.join("\n\n").trim(),
      headline: deduped.headline,
      subMarkdown: step?.sub ? String(step.sub).trim() : "",
    };
  }, [intro, step?.question, step?.sub, stepIndex]);

  useEffect(() => {
    if (open && showOtherInput) {
      requestAnimationFrame(() => otherInputRef.current?.focus());
    }
  }, [open, showOtherInput, stepIndex]);

  if (!open || !step) return null;

  const progressPct = ((stepIndex + 1) / Math.max(totalSteps, 1)) * 100;

  return (
    <div
      className="tf-clarifier-overlay open"
      role="dialog"
      aria-modal="true"
      aria-labelledby="tf-clarifier-title"
      onClick={() => onDismiss?.()}
    >
      <div
        className="tf-clarifier-panel"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="tf-clarifier-head">
          <div className="tf-clarifier-progress" aria-hidden>
            <span
              className="tf-clarifier-progress-fill"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <p className="tf-clarifier-step-label">
            Question {stepIndex + 1} of {totalSteps}
          </p>
        </header>

        <div className="tf-clarifier-body">
          {contextMarkdown ? (
            <div className="tf-clarifier-context">
              <TfMarkdown className="tf-clarifier-context-md">{contextMarkdown}</TfMarkdown>
            </div>
          ) : null}

          {headline ? (
            <div
              id="tf-clarifier-title"
              className="tf-clarifier-q"
              role="heading"
              aria-level={2}
            >
              <TfMarkdown className="tf-clarifier-q-md">{headline}</TfMarkdown>
            </div>
          ) : null}

          {subMarkdown ? (
            <div className="tf-clarifier-sub">
              <TfMarkdown className="tf-clarifier-sub-md">{subMarkdown}</TfMarkdown>
            </div>
          ) : null}

          <p className="tf-clarifier-pick-label">Pick one</p>
          <div className="tf-clarifier-options" role="radiogroup" aria-label="Answer choices">
            {step.choices.map((c, index) => {
              const picked = answer?.choiceId === c.id;
              return (
                <button
                  key={c.id}
                  type="button"
                  role="radio"
                  aria-checked={picked}
                  className={`tf-clarifier-option${picked ? " picked" : ""}`}
                  onClick={() => onPickChoice?.(c.id)}
                >
                  <span className="tf-clarifier-option-index" aria-hidden>
                    {index + 1}
                  </span>
                  <span className="tf-clarifier-option-body">
                    <span className="tf-clarifier-option-label">{c.label}</span>
                  </span>
                  <span className="tf-clarifier-option-check" aria-hidden>
                    {picked ? <Icon.Check width="16" height="16" /> : null}
                  </span>
                </button>
              );
            })}
          </div>

          {showOtherInput ? (
            <label className="tf-clarifier-other">
              <span className="tf-clarifier-other-label">Your answer</span>
              <textarea
                ref={otherInputRef}
                rows={3}
                placeholder="Type your answer…"
                value={answer?.customText || ""}
                onChange={(e) => onCustomText?.(e.target.value)}
              />
            </label>
          ) : null}
        </div>

        <footer className="tf-clarifier-foot">
          <button
            type="button"
            className="tf-btn ghost tf-clarifier-dismiss"
            onClick={() => onDismiss?.()}
          >
            Answer in chat instead
          </button>
          <div className="tf-clarifier-nav">
            <button
              type="button"
              className="tf-btn secondary"
              disabled={stepIndex <= 0}
              onClick={() => onBack?.()}
            >
              Back
            </button>
            {isLastStep ? (
              <button
                type="button"
                className="tf-btn primary"
                disabled={!canAdvance}
                onClick={() => onComplete?.()}
              >
                Done
              </button>
            ) : (
              <button
                type="button"
                className="tf-btn primary"
                disabled={!canAdvance}
                onClick={() => onNext?.()}
              >
                Next
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}
