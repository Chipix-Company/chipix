"""
ChatEngine — Cursor-like AI Coding Assistant for Verification.

Capabilities:
  1. EXPLAIN  — answer questions about design, UVM, RTL
  2. GENERATE — write new tests, sequences, assertions (show code)
  3. MODIFY   — change existing generated files ON DISK (like Cursor)
  4. VERIFY   — compile the changed file and report the result
  5. DEBUG    — analyse compile/sim errors and suggest fixes

The key difference vs a plain chatbot:
  - When the user says "add a test for X" or "change the driver to...",
    it actually writes the file, then compiles it, then tells you the result.
"""

from __future__ import annotations

import asyncio
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

import config
from core.ai_client import AIClient
from core.compiler import Compiler
from core.logger import get_logger

logger = get_logger("ChatEngine")


# ═══════════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ChatMessage:
    """A single chat message."""
    role: str          # "user" or "assistant"
    content: str
    timestamp: str = ""
    file_changed: str = ""     # Name of file changed (if any)
    compile_passed: bool | None = None  # Result of re-compile (None = not compiled)


@dataclass
class ChatSession:
    """A conversation session with full project context."""
    session_id: str = ""
    messages: list[ChatMessage] = field(default_factory=list)
    context: dict[str, str] = field(default_factory=dict)

    # Paths to real files on disk (for write-back)
    output_dir: str = "output/verification"
    rtl_dir: str = ""

    def add_user(self, text: str) -> None:
        self.messages.append(ChatMessage(role="user", content=text))

    def add_assistant(
        self,
        text: str,
        file_changed: str = "",
        compile_passed: bool | None = None,
    ) -> None:
        self.messages.append(ChatMessage(
            role="assistant", content=text,
            file_changed=file_changed, compile_passed=compile_passed,
        ))

    def get_history(self, max_turns: int = 10) -> str:
        recent = self.messages[-max_turns * 2:]
        return "\n".join(f"[{m.role.upper()}]: {m.content[:300]}" for m in recent)


# ═══════════════════════════════════════════════════════════════════════
# Intent Detection
# ═══════════════════════════════════════════════════════════════════════

MODIFY_KEYWORDS = [
    "add test", "add a test", "create test", "write a test",
    "add assertion", "add coverage", "add coverpoint",
    "change the", "modify the", "update the", "fix the",
    "add a sequence", "add sequence",
    "rewrite", "refactor",
    "increase coverage",
]

EXPLAIN_KEYWORDS = [
    "explain", "how does", "what is", "what does", "how do",
    "describe", "show me what", "walk me through",
]

DEBUG_KEYWORDS = [
    "why", "fail", "error", "broken", "not working",
    "debug", "issue", "problem",
]

GENERATE_KEYWORDS = [
    "generate", "create", "write", "show me the code",
    "give me a", "can you write", "create a new",
]


def detect_intent(message: str) -> str:
    """Detect what the user wants to do."""
    msg_lower = message.lower()

    for kw in MODIFY_KEYWORDS:
        if kw in msg_lower:
            return "modify"   # Will write file to disk + compile

    for kw in DEBUG_KEYWORDS:
        if kw in msg_lower:
            return "debug"

    for kw in GENERATE_KEYWORDS:
        if kw in msg_lower:
            return "generate"

    for kw in EXPLAIN_KEYWORDS:
        if kw in msg_lower:
            return "explain"

    if any(w in msg_lower for w in ["coverage", "coverpoint", "covergroup"]):
        return "coverage"
    if any(w in msg_lower for w in ["assertion", "property", "sva"]):
        return "assertion"
    if any(w in msg_lower for w in ["sequence", "stimulus", "randomize"]):
        return "sequence"

    return "general"


def identify_target_file(message: str, output_dir: str) -> str | None:
    """Find which generated file the user is referring to.

    Checks for keywords like 'driver', 'scoreboard', 'coverage' etc.
    and matches them to real files on disk.
    """
    out = Path(output_dir)
    if not out.exists():
        return None

    sv_files = list(out.glob("*.sv")) + list(out.glob("*.svh"))

    # Keywords → file suffix mapping
    kw_map = {
        "driver": "_driver.sv",
        "monitor": "_monitor.sv",
        "scoreboard": "_scoreboard.sv",
        "coverage": "_coverage.sv",
        "sequence": "_seq_lib.sv",
        "test": "_tests.sv",
        "base test": "_base_test.sv",
        "environment": "_env.sv",
        "env": "_env.sv",
        "agent": "_agent.sv",
        "interface": "_if.sv",
        "package": "_pkg.sv",
        "assertion": "_assertions.sv",
        "tb_top": "tb_top.sv",
        "top": "tb_top.sv",
        "testbench": "tb_top.sv",
        "makefile": "Makefile",
    }

    msg_lower = message.lower()
    for kw, suffix in kw_map.items():
        if kw in msg_lower:
            # Find the file matching this suffix
            for f in sv_files:
                if f.name.endswith(suffix) or f.name == suffix:
                    return str(f)

    return None


# ═══════════════════════════════════════════════════════════════════════
# System Prompts
# ═══════════════════════════════════════════════════════════════════════

CHAT_SYSTEM_PROMPT = """\
You are ChipVerify AI — an expert verification engineer assistant.
You work like Cursor or GitHub Copilot but specialized for RTL verification.

PERSONALITY:
- Direct and practical — give code, not lectures
- You KNOW the design: port names, FSM states, protocol rules
- When you modify code, you show a before/after diff
- When you add a test, you write the complete class, not pseudocode

CAPABILITIES:
1. EXPLAIN: Answer questions about the design, UVM, RTL, protocols
2. MODIFY:  Change existing verification files (driver, monitor, scoreboard...)
3. GENERATE: Write new sequences, tests, assertions, covergroups
4. DEBUG:   Diagnose compile/simulation failures

RULES:
- Use EXACT port/signal names from the RTL (see context)
- When generating SystemVerilog, wrap in ```systemverilog ```
- For modifications: ALWAYS show the FULL modified file, not a snippet
- Mark the specific changed section with // ← CHANGED HERE comments
- Be precise about which file you are modifying
"""

MODIFY_PROMPT = """\
The user wants you to MODIFY an existing verification file.

RULES:
1. Return the COMPLETE modified file (not just the changed part)
2. Mark every changed line with // ← CHANGED
3. Do NOT remove any existing functionality
4. Use EXACT signal/port names from the RTL
5. Wrap the complete file in ```systemverilog ... ```

CURRENT FILE CONTENT:
{file_content}

USER REQUEST:
{request}
"""


# ═══════════════════════════════════════════════════════════════════════
# ChatEngine
# ═══════════════════════════════════════════════════════════════════════

class ChatEngine:
    """Cursor-like AI coding assistant for verification.

    Unlike a plain chatbot:
      - MODIFY intent → writes file to disk, compiles, reports result
      - DEBUG intent  → reads compile errors, explains + suggests fix code
      - Maintains full project context across the conversation
    """

    def __init__(self, ai_client: AIClient) -> None:
        self.ai_client = ai_client
        self.sessions: dict[str, ChatSession] = {}
        self.compiler = Compiler()

    def create_session(
        self,
        session_id: str = "default",
        rtl_code: str = "",
        spec_text: str = "",
        mental_model_text: str = "",
        generated_files: dict[str, str] | None = None,
        output_dir: str = "output/verification",
        rtl_dir: str = "",
    ) -> ChatSession:
        """Create a session with full project context."""
        session = ChatSession(
            session_id=session_id,
            output_dir=output_dir,
            rtl_dir=rtl_dir,
        )

        if rtl_code:
            session.context["RTL DESIGN"] = rtl_code
        if spec_text:
            session.context["SPECIFICATION"] = spec_text
        if mental_model_text:
            session.context["MENTAL MODEL"] = mental_model_text

        # Load generated files FROM DISK if they exist
        out = Path(output_dir)
        if out.exists():
            for sv_file in sorted(out.glob("*.sv"))[:12]:  # cap at 12
                try:
                    content = sv_file.read_text(encoding="utf-8", errors="replace")
                    session.context[f"FILE:{sv_file.name}"] = content[:3000]  # 3K chars each
                except Exception:
                    pass

        # Also accept in-memory files
        if generated_files:
            for fname, content in generated_files.items():
                session.context[f"FILE:{fname}"] = content[:3000]

        self.sessions[session_id] = session
        logger.info(
            "ChatSession '%s' created: %d context blocks, output_dir=%s",
            session_id, len(session.context), output_dir,
        )
        return session

    async def chat(
        self,
        message: str,
        session_id: str = "default",
        force_intent: str | None = None,
    ) -> str:
        """Process user message. Cursor-like: actually writes and compiles.

        Args:
            message:      The user's text
            session_id:   Which session to use
            force_intent: If set (from UI mode dropdown), override keyword detection.
                          Values: 'general' | 'modify' | 'debug' | 'generate'
        """
        session = self.sessions.get(session_id)
        if not session:
            session = self.create_session(session_id)

        session.add_user(message)

        # Determine intent — dropdown overrides auto-detection
        if force_intent:
            intent = force_intent
            logger.info("Chat [%s] forced_intent=%s: %.60s", session_id, intent, message)
        else:
            intent = detect_intent(message)
            logger.info("Chat [%s] auto_intent=%s: %.60s", session_id, intent, message)

        # ── MODIFY: the Cursor capability ────────────────────────────
        if intent == "modify":
            return await self._handle_modify(message, session)

        # ── DEBUG: read errors from disk, explain + suggest fix ──────
        if intent == "debug":
            return await self._handle_debug(message, session)

        # ── Everything else: answer in natural language ───────────────
        return await self._handle_explain(message, session, intent)

    # ─────────────────────────────────────────────────────────────────
    # MODIFY handler — like Cursor's "inline edit"
    # ─────────────────────────────────────────────────────────────────

    async def _handle_modify(self, message: str, session: ChatSession) -> str:
        """Detect target file → generate new code → write to disk → compile → report."""

        # Step 1: Find which file to modify
        target_file = identify_target_file(message, session.output_dir)

        if not target_file or not Path(target_file).exists():
            # No matching file — just generate code and show it
            return await self._handle_explain(message, session, "generate")

        file_path = Path(target_file)
        original_content = file_path.read_text(encoding="utf-8", errors="replace")
        fname = file_path.name

        # Step 2: Ask AI to produce the modified file
        modify_instruction = MODIFY_PROMPT.format(
            file_content=original_content[:6000],
            request=message,
        )

        context = dict(session.context)
        context["CONVERSATION HISTORY"] = session.get_history()

        response = await self.ai_client.generate_with_context(
            system_prompt=CHAT_SYSTEM_PROMPT,
            context_blocks=context,
            focus_instruction=modify_instruction,
            task_instruction=message,
        )

        # Step 3: Extract the new code
        new_code = self._extract_sv_code(response)
        if not new_code:
            # Nothing valid extracted, return explanation only
            session.add_assistant(response)
            return response

        # Step 4: Write to disk (like Cursor auto-save)
        file_path.write_text(new_code, encoding="utf-8")
        logger.info("ChatEngine wrote %d lines to %s", len(new_code.splitlines()), fname)

        # Step 5: Compile to verify
        compile_result = self.compiler.compile_content(
            content=new_code,
            filename=fname,
        )

        # Step 6: Build the diff to show what changed
        diff = self._build_diff(original_content, new_code, fname)

        # Step 7: Build the response
        if compile_result.success:
            status_block = (
                f"\n\n✅ **Compiled successfully** — `{fname}` updated on disk."
                f"\n> {len(new_code.splitlines())} lines"
            )
        else:
            errors_txt = "\n".join(
                f"- Line {e.line}: `{e.message}`" for e in compile_result.errors[:5]
            )
            status_block = (
                f"\n\n⚠️ **Written to disk but compile errors found in `{fname}`:**\n"
                f"{errors_txt}\n"
                f"\nI'll fix these. Please ask me to 'fix the errors in {fname}'."
            )

        # Final reply: explanation + diff + compile result
        reply = (
            f"### ✏️ Modified `{fname}`\n\n"
            f"{self._extract_explanation(response)}\n\n"
            f"**Changes made:**\n```diff\n{diff[:2000]}\n```"
            f"{status_block}"
        )

        session.add_assistant(reply, file_changed=fname, compile_passed=compile_result.success)

        # Update context with new file content
        session.context[f"FILE:{fname}"] = new_code[:3000]

        return reply

    # ─────────────────────────────────────────────────────────────────
    # DEBUG handler
    # ─────────────────────────────────────────────────────────────────

    async def _handle_debug(self, message: str, session: ChatSession) -> str:
        """Read compile errors from disk and help debug."""

        # Try to pick up the last compile error from output dir
        error_context = ""
        out = Path(session.output_dir)
        for f in out.glob("*.sv"):
            result = self.compiler.compile_content(
                content=f.read_text(encoding="utf-8", errors="replace"),
                filename=f.name,
            )
            if not result.success:
                error_context += f"\n**{f.name}** errors:\n"
                for e in result.errors[:3]:
                    error_context += f"  - Line {e.line}: {e.message}\n"

        context = dict(session.context)
        context["CONVERSATION HISTORY"] = session.get_history()
        if error_context:
            context["CURRENT COMPILE ERRORS"] = error_context

        focus = (
            "The user is debugging a verification failure. "
            "Analyze the errors, explain the root cause clearly, "
            "and show the exact code fix needed."
        )

        response = await self.ai_client.generate_with_context(
            system_prompt=CHAT_SYSTEM_PROMPT,
            context_blocks=context,
            focus_instruction=focus,
            task_instruction=message,
        )

        session.add_assistant(response)
        return response

    # ─────────────────────────────────────────────────────────────────
    # EXPLAIN / GENERATE handler
    # ─────────────────────────────────────────────────────────────────

    async def _handle_explain(self, message: str, session: ChatSession, intent: str) -> str:
        guides = {
            "explain":   "Explain clearly with code examples. Reference exact signal names.",
            "generate":  "Generate complete, compilable SystemVerilog code. No placeholders.",
            "coverage":  "Generate covergroups with meaningful bins for corner cases.",
            "assertion": "Generate SVA properties with proper clocking and disable iff.",
            "sequence":  "Generate UVM sequences with body() using start_item/finish_item.",
            "general":   "Answer the verification question concisely and practically.",
            "debug":     "Identify root cause and show the exact code fix.",
        }
        focus = guides.get(intent, guides["general"])

        context = dict(session.context)
        context["CONVERSATION HISTORY"] = session.get_history()

        response = await self.ai_client.generate_with_context(
            system_prompt=CHAT_SYSTEM_PROMPT,
            context_blocks=context,
            focus_instruction=focus,
            task_instruction=message,
        )

        session.add_assistant(response)
        return response

    # ─────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────

    def _extract_sv_code(self, response: str) -> str:
        """Extract SystemVerilog code block from LLM response."""
        # Try ```systemverilog ... ```
        m = re.search(
            r"```(?:systemverilog|verilog|sv)[^\n]*\n(.*?)```",
            response, re.DOTALL | re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()
        # Generic code block
        m = re.search(r"```\w*\s*\n(.*?)```", response, re.DOTALL)
        if m:
            return m.group(1).strip()
        return ""

    def _extract_explanation(self, response: str) -> str:
        """Strip code blocks from response — keep only explanation text."""
        return re.sub(r"```.*?```", "", response, flags=re.DOTALL).strip()[:500]

    def _build_diff(self, original: str, modified: str, filename: str) -> str:
        """Build a unified diff between original and modified content."""
        diff_lines = list(difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            n=3,
        ))
        return "".join(diff_lines[:60])  # cap at 60 diff lines

    def update_context(self, session_id: str, key: str, value: str) -> None:
        """Update session context with new information."""
        session = self.sessions.get(session_id)
        if session:
            session.context[key] = value

    def get_session(self, session_id: str = "default") -> ChatSession | None:
        return self.sessions.get(session_id)
