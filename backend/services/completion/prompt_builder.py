"""FIM prompt building with snippet injection and budget accounting."""

from __future__ import annotations

from dataclasses import dataclass, field

from services.completion.config import CompletionConfig
from services.completion.schemas import Segments, Snippet


def normalize_line_endings(text: str) -> str:
    return str(text or "").replace("\r\n", "\n")


def default_suffix(suffix: str) -> str:
    value = normalize_line_endings(suffix)
    return value if value else "\n"


def format_snippet_comment(snippet: Snippet, language: str | None) -> str:
    path = snippet.filepath or "context"
    header = f"// --- context: {path} ---"
    body_lines = normalize_line_endings(snippet.body).splitlines()
    commented = "\n".join(f"// {line}" if line else "//" for line in body_lines)
    return f"{header}\n{commented}"


@dataclass
class PromptBuildResult:
    prompt: str
    snippets_used: list[str] = field(default_factory=list)
    budget_remaining: int = 0


class PromptBuilder:
    def __init__(self, config: CompletionConfig) -> None:
        self.config = config

    def collect_client_snippets(self, segments: Segments) -> list[Snippet]:
        ordered: list[Snippet] = []
        ordered.extend(segments.declarations or [])
        ordered.extend(segments.relevant_snippets_from_changed_files or [])
        ordered.extend(segments.relevant_snippets_from_recently_opened_files or [])
        ordered.extend(segments.relevant_snippets_from_spec or [])
        ordered.extend(segments.relevant_snippets_from_mental_model or [])
        return ordered

    def merge_snippets(
        self,
        snippets: list[Snippet],
        *,
        budget: int | None = None,
    ) -> tuple[list[str], int]:
        budget = self.config.snippet_budget if budget is None else max(0, budget)
        used: list[str] = []
        language = "systemverilog"

        for snippet in snippets:
            if budget <= 0:
                break
            block = format_snippet_comment(snippet, language)
            if len(block) > budget:
                block = block[:budget]
            if not block.strip():
                continue
            used.append(block)
            budget -= len(block)
        return used, budget

    def build(
        self,
        segments: Segments,
        snippet_blocks: list[str],
        *,
        raw_prompt: str | None = None,
    ) -> PromptBuildResult:
        if raw_prompt is not None:
            return PromptBuildResult(prompt=raw_prompt, snippets_used=snippet_blocks)

        prefix = normalize_line_endings(segments.prefix)
        suffix = default_suffix(segments.suffix)
        injected = "\n".join(snippet_blocks)
        if injected:
            prefix = f"{injected}\n{prefix}"

        template = self.config.fim_template
        prompt = template.format(prefix=prefix, suffix=suffix)

        max_chars = self.config.max_input_chars
        if len(prompt) > max_chars:
            prompt = prompt[-max_chars:]

        return PromptBuildResult(
            prompt=prompt,
            snippets_used=snippet_blocks,
            budget_remaining=max(0, self.config.snippet_budget - sum(len(s) for s in snippet_blocks)),
        )
