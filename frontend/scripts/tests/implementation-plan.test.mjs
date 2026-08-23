import assert from "node:assert/strict";
import test from "node:test";
import {
  normalizePlanPayload,
  planPayloadFromVerificationApi,
  IMPLEMENTATION_PLAN_ACTIONS,
} from "../../src/components/thread-first/implementationPlan.js";

test("normalizePlanPayload maps design fence fields", () => {
  const p = normalizePlanPayload({
    phase: "design",
    title: "RTL design plan",
    summary: "Async FIFO, 32-bit.",
    assumptions: ["Active-low reset"],
    steps: [{ id: "fifo", label: "sync_fifo.sv", deliverables: ["sync_fifo.sv"] }],
    constraints: ["SystemVerilog"],
  });
  assert.equal(p.phase, "design");
  assert.equal(p.title, "RTL design plan");
  assert.equal(p.summary, "Async FIFO, 32-bit.");
  assert.equal(p.assumptions.length, 1);
  assert.equal(p.steps.length, 1);
  assert.equal(p.steps[0].label, "sync_fifo.sv");
  assert.equal(p.actions[0].label, "Implement");
});

test("planPayloadFromVerificationApi uses presentation layer", () => {
  const p = planPayloadFromVerificationApi(
    {
      verification_type: "unitsim",
      module_name: "fifo",
      presentation: {
        summary: "I understand a synchronous FIFO with gray pointers.",
        assumptions: ["Single clock"],
        warnings: ["No SVA in RTL yet"],
      },
      unitsim: { total_scenarios: 3, scenarios: [{ name: "reset" }] },
    },
    "unitsim",
    { top_module: "fifo" },
  );
  assert.equal(p.phase, "verification");
  assert.match(p.summary, /FIFO/);
  assert.equal(p.assumptions[0], "Single clock");
  assert.ok(p.risks.some((r) => /SVA/.test(r)));
  assert.ok(p.steps.some((s) => /Unit simulation/.test(s.label)));
  assert.equal(p._source, "staged");
});

test("planPayloadFromVerificationApi formats sections and filters TOC noise", () => {
  const p = planPayloadFromVerificationApi(
    {
      verification_type: "uvm",
      uvm: { top_module: "Bridge_Top", sequences: [] },
      presentation: {
        summary: [
          "I will verify the bridge.",
          "**Design understanding**",
          "- AHB to APB bridge",
          "**Requirements in scope**",
          "- REQ-001: Technical Requirements .................................................................... 3",
          "- REQ-002: Project Management Needs ................................................................ 5",
          "- REQ-003: The bridge shall latch address and data.",
          "**Planned test cases**",
          "- reset_sequence: Apply reset",
        ].join("\n"),
      },
    },
    "uvm",
  );

  assert.match(p.summary, /\n\n### Design understanding\n/);
  assert.match(p.summary, /\n\n### Planned test cases\n/);
  assert.doesNotMatch(p.summary, /Technical Requirements/);
  assert.doesNotMatch(p.summary, /Project Management Needs/);
  assert.match(p.summary, /bridge shall latch address/);
});

test("planPayloadFromVerificationApi humanizes object-shaped risks", () => {
  const p = planPayloadFromVerificationApi(
    {
      verification_type: "uvm",
      uvm: { top_module: "Bridge_Top", sequences: [] },
      presentation: {
        summary: "I will verify the bridge.",
        risks: [
          {
            question: "Should Prdata be constrained to unsigned values?",
            context: "APB_Interface",
            source_ref: { file: "rtl_test/APB_Interface.v", line: 0 },
          },
          {
            type: "nondeterminism",
            description: "Prdata uses $random in simulation.",
          },
        ],
      },
    },
    "uvm",
  );

  assert.ok(p.risks.some((r) => /APB_Interface: Should Prdata/.test(r)));
  assert.ok(p.risks.some((r) => /nondeterminism: Prdata uses/.test(r)));
  assert.ok(!p.risks.some((r) => /\[object Object\]|\{'question'/.test(r)));
});

test("IMPLEMENTATION_PLAN_ACTIONS has approve and refine", () => {
  assert.equal(IMPLEMENTATION_PLAN_ACTIONS[0].id, "approve");
  assert.equal(IMPLEMENTATION_PLAN_ACTIONS[1].id, "refine");
});
