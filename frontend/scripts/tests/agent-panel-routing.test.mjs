import test from "node:test";
import assert from "node:assert/strict";
import {
  isVerificationRunCommand,
  shouldStartVerificationFlow,
} from "../../src/components/agentPanelRouting.js";

test("recognizes common verification run commands", () => {
  assert.equal(isVerificationRunCommand("run verification now"), true);
  assert.equal(isVerificationRunCommand("start verification"), true);
  assert.equal(isVerificationRunCommand("strt verifiacation"), true);
  assert.equal(isVerificationRunCommand("launch simulation"), true);
});

test("starts verification setup for setup commands", () => {
  assert.equal(shouldStartVerificationFlow("run verification workflow", ""), true);
  assert.equal(shouldStartVerificationFlow("start verification setup", "verification"), true);
});

test("does not restart setup when verification mode receives a run command", () => {
  assert.equal(shouldStartVerificationFlow("run verification now", "verification"), false);
  assert.equal(shouldStartVerificationFlow("start verification", "verification"), false);
});

test("does not treat run commands as setup commands even before flow mode is active", () => {
  assert.equal(shouldStartVerificationFlow("run verification now", ""), false);
  assert.equal(shouldStartVerificationFlow("start verification", ""), false);
});
