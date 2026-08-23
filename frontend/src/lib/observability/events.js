/**
 * PostHog event names for ChipVerify beta.
 *
 * Dashboard suggestions (PostHog):
 * - Funnel: app.started → project.selected → mental_model.build_completed → plan.implement_clicked → staged.execute_completed
 * - Retention: weekly active users with verification.run_created
 * - Error correlation: filter sessions where rtl_agent.error or verification.run_failed occurred
 */

export const AnalyticsEvents = Object.freeze({
  APP_STARTED: "app.started",

  ONBOARDING_TOUR_STARTED: "onboarding.tour_started",
  ONBOARDING_STEP_VIEWED: "onboarding.step_viewed",
  ONBOARDING_TOUR_COMPLETED: "onboarding.tour_completed",
  ONBOARDING_TOUR_SKIPPED: "onboarding.tour_skipped",

  AUTH_SESSION_STARTED: "auth.session_started",
  AUTH_SESSION_FAILED: "auth.session_failed",

  ACTIVATION_COMPLETED: "activation.completed",

  PROJECT_CREATED: "project.created",
  PROJECT_SELECTED: "project.selected",

  MENTAL_MODEL_BUILD_STARTED: "mental_model.build_started",
  MENTAL_MODEL_BUILD_COMPLETED: "mental_model.build_completed",
  MENTAL_MODEL_BUILD_FAILED: "mental_model.build_failed",

  CLARIFIER_OPENED: "clarifier.opened",
  CLARIFIER_COMPLETED: "clarifier.completed",
  CLARIFIER_DISMISSED: "clarifier.dismissed",

  PLAN_RECEIVED: "plan.received",
  PLAN_IMPLEMENT_CLICKED: "plan.implement_clicked",
  PLAN_IMPROVE_CLICKED: "plan.improve_clicked",

  STAGED_PREPARE_COMPLETED: "staged.prepare_completed",
  STAGED_STRATEGY_SELECTED: "staged.strategy_selected",
  STAGED_PLAN_GENERATED: "staged.plan_generated",
  STAGED_EXECUTE_STARTED: "staged.execute_started",
  STAGED_EXECUTE_COMPLETED: "staged.execute_completed",
  STAGED_EXECUTE_FAILED: "staged.execute_failed",

  RTL_AGENT_MESSAGE_SENT: "rtl_agent.message_sent",
  RTL_AGENT_STREAM_COMPLETED: "rtl_agent.stream_completed",
  RTL_AGENT_ERROR: "rtl_agent.error",

  VERIFICATION_RUN_CREATED: "verification.run_created",
  VERIFICATION_RUN_COMPLETED: "verification.run_completed",
  VERIFICATION_RUN_FAILED: "verification.run_failed",

  FOUNDERS_LETTER_OPENED: "founders.letter_opened",
  FOUNDERS_FEEDBACK_OPENED: "founders.feedback_opened",

  /** PRD-aligned aliases */
  JOB_CREATED: "job.created",
  JOB_COMPLETED: "job.completed",
  JOB_FAILED: "job.failed",
});
