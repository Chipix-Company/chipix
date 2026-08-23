import React, { useCallback } from "react";
import Icon from "../icons";
import useOverlayClose from "../useOverlayClose";
import { AnalyticsEvents, track } from "../../../lib/observability";
import {
  isUserJotConfigured,
  openUserJot,
  openUserJotPublicBoard,
} from "../../../lib/userjot";

const FEEDBACK_ACTIONS = [
  {
    id: "feedback",
    label: "Share feedback",
    hint: "Tell us what to build, fix, or improve",
    Ico: Icon.Edit,
    primary: true,
  },
  {
    id: "roadmap",
    label: "Product roadmap",
    hint: "See what we are working on and vote on ideas",
    Ico: Icon.Layers,
  },
  {
    id: "updates",
    label: "Changelog",
    hint: "Track releases and what shipped recently",
    Ico: Icon.Sparkles,
  },
];

/**
 * FoundersOverlay — a personal note inviting users to shape the product via UserJot.
 */
export default function FoundersOverlay({ open, onClose, isDarkTheme = false }) {
  const { closeButtonProps } = useOverlayClose({
    open,
    onClose,
    closeOnEsc: false,
    label: "Close letter from the founders",
  });

  const handleAction = useCallback((section) => {
    track(AnalyticsEvents.FOUNDERS_FEEDBACK_OPENED, { section });
    if (isUserJotConfigured()) {
      openUserJot(section);
    } else {
      openUserJotPublicBoard({ section, openFeedback: section === "feedback" });
    }
  }, []);

  if (!open) return null;

  return (
    <div
      className="tf-overlay tf-founders-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Letter from the founders"
      data-tf-theme={isDarkTheme ? "dark" : "light"}
    >
      <header className="tf-overlay-head tf-founders-head">
        <div className="tf-overlay-title">
          <Icon.Heart width="18" height="18" />
          <span>Letter from the founders</span>
        </div>
        <button type="button" className="tf-overlay-close" {...closeButtonProps}>
          <Icon.Close width="14" height="14" />
        </button>
      </header>

      <div className="tf-founders-body">
        <article className="tf-founders-letter">
          <p className="tf-founders-salutation">Hi there,</p>

          <p>
            We built ChipVerify because verification work is hard — and because the people
            doing it every day deserve tools that actually understand their designs, not
            generic assistants that guess at RTL.
          </p>

          <p>
            <strong>You are not just using our product. You are helping us build it.</strong>
            {" "}
            Every bug report, every &ldquo;this workflow feels wrong,&rdquo; every feature
            idea from someone who lives in testbenches and spec sheets — that is how ChipVerify
            gets better. We read everything. We genuinely want you in the loop.
          </p>

          <p>
            Whether you are exploring the beta, running your first staged verification, or
            pushing the agent on a real block — your perspective matters more than any roadmap
            slide in a deck. Tell us what is working, what is confusing, and what would make
            this indispensable for your team.
          </p>

          <p className="tf-founders-signoff">
            Thank you for being early. We are building this with you.
            <span className="tf-founders-signature">— The ChipVerify team</span>
          </p>
        </article>

        <aside className="tf-founders-actions" aria-label="Feedback and product updates">
          <h2 className="tf-founders-actions-title">Shape what we ship next</h2>
          <p className="tf-founders-actions-lead">
            Submit ideas, vote on the roadmap, and follow releases — all tied to your account
            so we can follow up when it matters.
          </p>

          <div className="tf-founders-action-grid">
            {FEEDBACK_ACTIONS.map(({ id, label, hint, Ico, primary }) => (
              <button
                key={id}
                type="button"
                className={`tf-founders-action${primary ? " primary" : ""}`}
                onClick={() => handleAction(id)}
              >
                <span className="tf-founders-action-icon" aria-hidden>
                  <Ico width="18" height="18" />
                </span>
                <span className="tf-founders-action-text">
                  <span className="tf-founders-action-label">{label}</span>
                  <span className="tf-founders-action-hint">{hint}</span>
                </span>
                <Icon.ArrowRight width="16" height="16" className="tf-founders-action-arrow" />
              </button>
            ))}
          </div>

          {!isUserJotConfigured() && (
            <p className="tf-founders-config-note">
              Feedback portal is not configured in this build. Set{" "}
              <code>VITE_USERJOT_PROJECT_ID</code> to enable the in-app widget.
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}
