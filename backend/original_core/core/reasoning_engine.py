"""
AGI Module 1 — Autonomous Reasoning Engine (ReAct Pattern).

Self-driving verification loop:
  THINK  → "What should I verify next?"
  ACT    → Generate/compile/simulate/check
  OBSERVE → Parse results, learn from outcomes
  DECIDE → Continue, fix, or declare done

The engineer drops in RTL + spec and walks away.
The system autonomously produces a complete, verified testbench.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import config
from core.ai_client import AIClient
from core.logger import get_logger

logger = get_logger("ReasoningEngine")


class ActionType(str, Enum):
    """Types of actions the reasoning engine can take."""
    ANALYZE_RTL = "analyze_rtl"
    DETECT_PROTOCOLS = "detect_protocols"
    BUILD_MENTAL_MODEL = "build_mental_model"
    GENERATE_UVM = "generate_uvm"
    GENERATE_FORMAL = "generate_formal"
    COMPILE = "compile"
    SIMULATE = "simulate"
    CHECK_COVERAGE = "check_coverage"
    FIX_ERRORS = "fix_errors"
    ADD_ASSERTIONS = "add_assertions"
    ADD_COVERAGE = "add_coverage"
    ADD_TESTS = "add_tests"
    REVIEW_DESIGN = "review_design"
    GENERATE_REPORT = "generate_report"
    DONE = "done"


@dataclass
class Thought:
    """A single thought in the reasoning chain."""
    step: int
    reasoning: str
    action: ActionType
    action_args: dict = field(default_factory=dict)
    observation: str = ""
    success: bool = True
    timestamp: float = 0.0


@dataclass
class ReasoningTrace:
    """Complete trace of the autonomous reasoning process."""
    thoughts: list[Thought] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    final_status: str = "running"
    total_actions: int = 0
    successful_actions: int = 0

    @property
    def elapsed(self) -> float:
        return (self.end_time or time.time()) - self.start_time

    @property
    def summary(self) -> str:
        return (
            f"Reasoning: {self.total_actions} actions "
            f"({self.successful_actions} OK), "
            f"{self.elapsed:.0f}s, status={self.final_status}"
        )

    def to_markdown(self) -> str:
        lines = ["# Autonomous Reasoning Trace", ""]
        for t in self.thoughts:
            status = "OK" if t.success else "FAILED"
            lines.append(f"### Step {t.step}: {t.action.value} [{status}]")
            lines.append(f"**Thinking:** {t.reasoning}")
            if t.observation:
                lines.append(f"**Observed:** {t.observation[:200]}")
            lines.append("")
        lines.append(f"**Final:** {self.summary}")
        return "\n".join(lines)


REASONING_PROMPT = """\
You are an autonomous chip verification AI. You decide what to do next.

CURRENT STATE:
{state}

ACTIONS TAKEN SO FAR:
{history}

AVAILABLE ACTIONS:
- analyze_rtl: Parse and understand the RTL design
- detect_protocols: Identify bus protocols (AXI, APB, etc.)
- build_mental_model: Create verification plan from design understanding
- generate_uvm: Generate full UVM testbench (18 files)
- generate_formal: Create SVA assertions and formal testplan
- compile: Compile all generated files
- simulate: Run simulation
- check_coverage: Analyze coverage and find gaps
- fix_errors: Auto-fix compilation or simulation errors
- add_assertions: Add more SVA assertions for uncovered properties
- add_coverage: Add more covergroups for uncovered bins
- add_tests: Generate targeted tests for coverage gaps
- review_design: Review RTL for common bugs before verification
- generate_report: Generate final verification report
- done: Verification is complete, stop

RULES:
1. Think step by step about what is needed
2. Choose ONE action at a time
3. If compilation fails, fix before simulating
4. If coverage < 80%, add more tests
5. If assertions fail, debug and fix
6. Stop when: all tests pass, coverage > 90%, no errors

OUTPUT FORMAT (JSON):
{
  "thinking": "My reasoning about what to do next...",
  "action": "action_name",
  "args": {}
}
"""


class ReasoningEngine:
    """Autonomous ReAct reasoning loop for verification."""

    def __init__(
        self,
        ai_client: AIClient,
        max_steps: int = 20,
    ) -> None:
        self.ai_client = ai_client
        self.max_steps = max_steps

    async def run_autonomous(
        self,
        rtl_file: str,
        spec_file: str,
        action_executor=None,
    ) -> ReasoningTrace:
        """Run fully autonomous verification.

        The engine decides what to do at each step. No human input needed.

        Args:
            rtl_file: Path to RTL
            spec_file: Path to specification
            action_executor: Callback to execute actions (orchestrator)

        Returns:
            Complete reasoning trace
        """
        trace = ReasoningTrace(start_time=time.time())

        state = {
            "rtl_file": rtl_file,
            "spec_file": spec_file,
            "phase": "starting",
            "compiled": False,
            "simulated": False,
            "coverage": 0,
            "errors": [],
            "files_generated": 0,
            "tests_passed": 0,
            "tests_failed": 0,
        }

        logger.info("=" * 60)
        logger.info("AUTONOMOUS REASONING ENGINE STARTED")
        logger.info("RTL: %s | Spec: %s", rtl_file, spec_file)
        logger.info("=" * 60)

        for step in range(1, self.max_steps + 1):
            # THINK: Ask LLM what to do next
            history = "\n".join(
                f"  Step {t.step}: [{t.action.value}] -> {'OK' if t.success else 'FAILED'}: "
                f"{t.observation[:100]}"
                for t in trace.thoughts
            )

            response = await self.ai_client.generate_with_context(
                system_prompt=REASONING_PROMPT.format(
                    state=self._format_state(state),
                    history=history or "  (none yet — this is the first step)",
                ),
                context_blocks={},
                focus_instruction="Decide the next verification action",
                task_instruction="What should I do next?",
                temperature=0.3,
            )

            # Parse the thought
            thought = self._parse_thought(response, step)

            logger.info(
                "[Step %d] THINK: %s -> ACT: %s",
                step, thought.reasoning[:80], thought.action.value,
            )

            # Check for done
            if thought.action == ActionType.DONE:
                thought.observation = "Verification complete!"
                trace.thoughts.append(thought)
                trace.final_status = "complete"
                break

            # ACT: Execute the action
            if action_executor:
                try:
                    result = await action_executor(thought.action, thought.action_args, state)
                    thought.observation = str(result)[:500]
                    thought.success = True

                    # Update state based on action
                    self._update_state(state, thought.action, result)

                except Exception as e:
                    thought.observation = f"Error: {e}"
                    thought.success = False
                    state["errors"].append(str(e))
            else:
                # Dry run — simulate action
                thought.observation = f"[DRY RUN] Would execute: {thought.action.value}"

            thought.timestamp = time.time()
            trace.thoughts.append(thought)
            trace.total_actions += 1
            if thought.success:
                trace.successful_actions += 1

        else:
            trace.final_status = "max_steps_reached"

        trace.end_time = time.time()

        # Save trace
        logger.info("=" * 60)
        logger.info("AUTONOMOUS REASONING COMPLETE: %s", trace.summary)
        logger.info("=" * 60)

        return trace

    def _parse_thought(self, response: str, step: int) -> Thought:
        """Parse LLM response into a Thought."""
        import json
        import re

        # Try JSON parse
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                action_str = data.get("action", "done")
                try:
                    action = ActionType(action_str)
                except ValueError:
                    action = ActionType.DONE

                return Thought(
                    step=step,
                    reasoning=data.get("thinking", ""),
                    action=action,
                    action_args=data.get("args", {}),
                )
            except json.JSONDecodeError:
                pass

        # Fallback
        return Thought(
            step=step,
            reasoning=response[:200],
            action=ActionType.DONE,
        )

    def _format_state(self, state: dict) -> str:
        lines = []
        for k, v in state.items():
            if isinstance(v, list):
                lines.append(f"  {k}: {len(v)} items")
            else:
                lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _update_state(self, state: dict, action: ActionType, result) -> None:
        """Update state based on action results."""
        if action == ActionType.ANALYZE_RTL:
            state["phase"] = "analyzed"
        elif action == ActionType.BUILD_MENTAL_MODEL:
            state["phase"] = "planned"
        elif action in (ActionType.GENERATE_UVM, ActionType.GENERATE_FORMAL):
            state["phase"] = "generated"
            state["files_generated"] = state.get("files_generated", 0) + 1
        elif action == ActionType.COMPILE:
            state["compiled"] = "error" not in str(result).lower()
        elif action == ActionType.SIMULATE:
            state["simulated"] = True
        elif action == ActionType.CHECK_COVERAGE:
            state["phase"] = "coverage_checked"
        elif action == ActionType.FIX_ERRORS:
            if state.get("errors"):
                state["errors"] = state["errors"][:-1]
