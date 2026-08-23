"""
Agent Tools — Tool definitions for the agentic loop.

Each tool wraps existing verification logic (from original_core/agents)
and exposes it as a callable function for the LLM agent.

Tool JSON definitions are registered in VERIFICATION_TOOL_DEFINITIONS.
Tool execution functions are registered in VERIFICATION_TOOL_HANDLERS.
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine, Dict, List

# ═══════════════════════════════════════════════════════════════════════
# Tool Definitions (JSON Schema for native function calling)
# ═══════════════════════════════════════════════════════════════════════

VERIFICATION_TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "scanProject",
            "description": (
                "Scan a project folder to understand its structure. "
                "Returns file counts, RTL files, specs, hierarchy, and estimated complexity. "
                "Use this FIRST before building a mental model."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buildMentalModel",
            "description": (
                "Build a mental model from RTL + specification. "
                "Parses design structure, identifies requirements, clock domains, "
                "protocols, FSMs, and generates a verification plan. "
                "The mental model becomes the context for all verification agents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "target_module": {
                        "type": "string",
                        "description": "Optional: specific module to focus on",
                    },
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "queryMentalModel",
            "description": (
                "Query the current mental model for design information. "
                "Returns module details, requirements, verification plan, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "query": {
                        "type": "string",
                        "description": "What to query: 'summary', 'requirements', 'ports', 'hierarchy', 'verification_plan', 'open_questions'",
                    },
                },
                "required": ["project_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generateTestbench",
            "description": (
                "Generate a SystemVerilog testbench for unit-level simulation. "
                "Uses the mental model to create directed tests, boundary checks, "
                "and response checkers. No UVM dependency."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "target_module": {
                        "type": "string",
                        "description": "Module to generate testbench for",
                    },
                    "test_type": {
                        "type": "string",
                        "enum": ["directed", "random", "boundary", "all"],
                        "description": "Type of tests to generate",
                    },
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "runUnitSimulation",
            "description": (
                "Compile and run a unit-level simulation using iverilog or Verilator. "
                "Returns pass/fail results, assertion failures, and log excerpts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generateFormalProperties",
            "description": (
                "Generate SystemVerilog Assertions (SVA) for formal verification. "
                "Creates mental-model-grounded assert/assume/cover properties, "
                "uses the configured LLM to improve the formal plan when available, "
                "and produces bind modules plus tool scripts (sby for SymbiYosys, Tcl for Jasper)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "target_module": {
                        "type": "string",
                        "description": "Module to generate formal properties for",
                    },
                    "property_types": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["assert", "assume", "cover"]},
                        "description": "Types of properties to generate",
                    },
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "runFormalVerification",
            "description": (
                "Run formal verification using SymbiYosys (open-source). "
                "Uses the mental model to resolve RTL source files, generates missing "
                "formal collateral when needed, and returns proof/fail/bounded results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "target_module": {
                        "type": "string",
                        "description": "Optional module/top to verify",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["bmc", "prove", "cover"],
                        "description": "Formal task mode",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Bound/proof depth for the formal run",
                    },
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generateUVMEnvironment",
            "description": (
                "Generate a complete UVM verification environment (18 files). "
                "Includes agent, driver, monitor, scoreboard, sequences, coverage, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "mode": {
                        "type": "string",
                        "enum": ["create", "update"],
                        "description": "Create new UVM env or update existing",
                    },
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyzeCoverage",
            "description": (
                "Analyze verification coverage results. "
                "Identifies coverage gaps and suggests additional tests."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "coverage_data": {
                        "type": "string",
                        "description": "Coverage report text or path",
                    },
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "debugFailure",
            "description": (
                "Analyze a verification failure. Classifies the failure type "
                "(syntax/timing/RTL bug/testbench bug), identifies root cause, "
                "and proposes a fix with a diff preview."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "error_log": {
                        "type": "string",
                        "description": "Simulation or formal tool error output",
                    },
                },
                "required": ["project_id", "error_log"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyzeDesign",
            "description": (
                "STEP 1 of verification: Analyze a design block. "
                "Scans the project folder, builds a mental model, and returns "
                "a summary of the design (ports, FSMs, protocols, requirements). "
                "Also returns available verification options. "
                "IMPORTANT: After calling this, present the summary to the user "
                "and ASK which verification they want to run. "
                "Do NOT automatically run verification — wait for user's choice. "
                "Use this when the user says 'verify this block' or 'verify the FIFO'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "target_module": {
                        "type": "string",
                        "description": "Optional: specific module to analyze",
                    },
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "runVerification",
            "description": (
                "STEP 2 of verification: Run a specific verification type "
                "AFTER the user has reviewed the design analysis and chosen what to run. "
                "Options: 'unitsim' (unit simulation), 'formal' (SVA + formal proofs), "
                "'uvm' (full UVM environment), or 'all' (everything). "
                "Only call this AFTER analyzeDesign and AFTER the user confirms their choice."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "target_module": {
                        "type": "string",
                        "description": "Module to verify (from analyzeDesign result)",
                    },
                    "verification_type": {
                        "type": "string",
                        "enum": ["unitsim", "formal", "uvm", "all"],
                        "description": "Which verification to run (user's choice)",
                    },
                },
                "required": ["project_id", "verification_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnoseAndFix",
            "description": (
                "SELF-HEALING: When verification FAILS, call this to analyze the failure, "
                "diagnose the root cause, and propose a fix with a diff preview. "
                "The fix will be shown to the user for approval before applying. "
                "After the user approves, call runVerification again to re-test. "
                "Use this whenever runVerification returns failures."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "error_log": {
                        "type": "string",
                        "description": "The error output from the failed verification run",
                    },
                    "verification_type": {
                        "type": "string",
                        "enum": ["unitsim", "formal", "uvm"],
                        "description": "Which verification type failed",
                    },
                },
                "required": ["project_id", "error_log"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detectDesignChanges",
            "description": (
                "EVOLVE Step 1: Compare old and new RTL to detect structural changes. "
                "Identifies port additions/removals, FSM state changes, and logic modifications. "
                "Use this when the user says 'I changed the RTL' or 'design was updated'. "
                "After detecting changes, present them and ask if the user wants to evolve."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "old_rtl": {
                        "type": "string",
                        "description": "The previous RTL source code",
                    },
                    "new_rtl": {
                        "type": "string",
                        "description": "The updated RTL source code",
                    },
                },
                "required": ["project_id", "old_rtl", "new_rtl"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evolveVerification",
            "description": (
                "EVOLVE Step 2: Auto-adapt the verification environment after RTL changes. "
                "Heals testbenches (interface, driver, monitor, coverage), "
                "updates the mental model, predicts which tests will fail, "
                "and generates a minimal regression suite. "
                "Call this after detectDesignChanges when the user approves."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "old_rtl": {
                        "type": "string",
                        "description": "The previous RTL source code",
                    },
                    "new_rtl": {
                        "type": "string",
                        "description": "The updated RTL source code",
                    },
                },
                "required": ["project_id", "new_rtl"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parseDesignImage",
            "description": (
                "MULTIMODAL: Parse a design diagram image (block diagram, hand-drawn "
                "state machine, whiteboard sketch, pin table) into structured mental model data. "
                "Sends the image to a Vision LLM and extracts modules, ports, FSMs, protocols, "
                "transaction flows, and hierarchy. "
                "Use when the user uploads or references an image file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "image_path": {
                        "type": "string",
                        "description": "Path to the image file (.png, .jpg, .webp)",
                    },
                    "additional_context": {
                        "type": "string",
                        "description": "Optional text context to help interpret the diagram",
                    },
                },
                "required": ["project_id", "image_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tuneTestPlan",
            "description": (
                "PLAN TUNING: Modify a test plan using natural language feedback. "
                "Supports: add new scenarios, remove scenarios, modify priority, "
                "adjust test parameters. Call this AFTER showing the test plan and "
                "BEFORE generating the testbench, when the user wants changes. "
                "Examples: 'Add an overflow test', 'Remove the stress test', "
                "'Make reset hold for 20 cycles', 'Increase FSM test priority to critical'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "plan_json": {
                        "type": "string",
                        "description": "Current test plan as JSON string",
                    },
                    "feedback": {
                        "type": "string",
                        "description": "Natural language feedback from the user",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["tune", "add", "remove", "prioritize", "show"],
                        "description": "Type of action (default: 'tune' for NL parsing)",
                    },
                    "scenario_id": {
                        "type": "string",
                        "description": "Optional scenario ID for targeted changes",
                    },
                },
                "required": ["project_id", "plan_json", "feedback"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getVerificationDashboard",
            "description": (
                "DASHBOARD: Automated tool that reads verification output files "
                "(simulation.log, coverage.json, formal_results.json, test_plan.json) "
                "directly from the output directory and aggregates them into a unified "
                "dashboard. No LLM processing — pure file reading and parsing. "
                "Call this AFTER all verification runs complete."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "module_name": {"type": "string", "description": "Design module name"},
                    "output_dir": {
                        "type": "string",
                        "description": "Path to verification output directory containing result files",
                    },
                },
                "required": ["project_id", "module_name", "output_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "queryCodebaseGraph",
            "description": (
                "Query the project codebase knowledge graph for structural questions "
                "(module hierarchy, spec-to-RTL links, instantiations). Prefer this over "
                "reading entire RTL trees for navigation questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "question": {
                        "type": "string",
                        "description": "Natural language question about design structure",
                    },
                },
                "required": ["project_id", "question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getModuleNeighbors",
            "description": "Return 1-hop neighbors of a module in the codebase graph.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "module_name": {"type": "string", "description": "Module name to inspect"},
                    "hops": {"type": "integer", "description": "Traversal depth (default 1)"},
                },
                "required": ["project_id", "module_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getAffectedByChange",
            "description": (
                "Reverse impact analysis: what modules/specs are affected if this node changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "node_label": {
                        "type": "string",
                        "description": "Module or spec node label",
                    },
                },
                "required": ["project_id", "node_label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getGraphGodNodes",
            "description": "High-centrality nodes in the codebase graph (good triage starting points).",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                    "limit": {"type": "integer", "description": "Max nodes to return"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getCodebaseGraphStatus",
            "description": "Check whether the codebase graph is built, building, or missing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project UUID"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "checkCadenceStatus",
            "description": (
                "Check whether Cadence Xcelium (xrun) is connected and licensed on this host. "
                "Call this before runCadenceSimulation when UVM collateral already exists. "
                "No project_id needed — uses the active session project automatically when required."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "runCadenceSimulation",
            "description": (
                "Run the full Cadence Xcelium pipeline (compile, elaborate, simulate) on "
                "existing generated UVM artifacts. Does NOT regenerate UVM. The active "
                "project and staged artifact IDs are bound server-side from session context. "
                "Returns feedback_memory for self-healing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional — server uses the active session project when omitted",
                    },
                    "generated_artifact_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional — server uses staged artifact IDs from context when omitted",
                    },
                    "top_module": {
                        "type": "string",
                        "description": "Simulation top module (default top_tb)",
                    },
                    "uvm_testname": {
                        "type": "string",
                        "description": "UVM test class name (+UVM_TESTNAME)",
                    },
                    "max_repair_rounds": {
                        "type": "integer",
                        "description": "Auto-repair rounds during compile (default 3)",
                    },
                    "auto_repair": {
                        "type": "boolean",
                        "description": "Enable sandbox auto-repair during compile",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getCadenceRunStatus",
            "description": "Poll status, phase, and log excerpt for an in-flight Cadence run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional — server uses the active session project when omitted",
                    },
                    "run_id": {"type": "string", "description": "Simulator run id"},
                },
                "required": ["run_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "applyCadenceFixes",
            "description": (
                "Apply repair diffs from a Cadence run back to project generated artifacts. "
                "Use after runCadenceSimulation fails and before re-running simulation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional — server uses the active session project when omitted",
                    },
                    "run_id": {"type": "string", "description": "Cadence run id with repair_history"},
                    "repair_history": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Optional repair_history from runCadenceSimulation",
                    },
                },
                "required": [],
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════════════
# Tool Handler Registry
# ═══════════════════════════════════════════════════════════════════════

# Maps tool name → async handler function
# Populated by each agent_tools module on import
VERIFICATION_TOOL_HANDLERS: Dict[
    str, Callable[..., Coroutine[Any, Any, str]]
] = {}


def register_tool(name: str, handler: Callable[..., Coroutine[Any, Any, str]]) -> None:
    """Register a tool handler by name."""
    VERIFICATION_TOOL_HANDLERS[name] = handler


def get_all_tool_definitions() -> List[Dict[str, Any]]:
    """Return all tool definitions for the agentic loop."""
    return VERIFICATION_TOOL_DEFINITIONS


def get_tool_handler(name: str) -> Callable[..., Coroutine[Any, Any, str]] | None:
    """Get a tool handler by name."""
    return VERIFICATION_TOOL_HANDLERS.get(name)
