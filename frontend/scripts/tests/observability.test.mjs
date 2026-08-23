import assert from "node:assert/strict";
import test from "node:test";

import { AnalyticsEvents } from "../../src/lib/observability/events.js";

test("AnalyticsEvents exposes beta funnel events", () => {
  assert.equal(AnalyticsEvents.MENTAL_MODEL_BUILD_COMPLETED, "mental_model.build_completed");
  assert.equal(AnalyticsEvents.PLAN_IMPLEMENT_CLICKED, "plan.implement_clicked");
  assert.equal(AnalyticsEvents.JOB_CREATED, "job.created");
});
