"""Language-aware stop conditions for completion streaming (Tabby-style reversed trie)."""

from __future__ import annotations

from dataclasses import dataclass, field


def _default_stop_sequences(language: str | None) -> list[str]:
    universal = [
        "\n\n",
        "\n\n  ",
        "<fim_prefix>",
        "<fim_suffix>",
        "<|fim_suffix|>",
        "<|file_sep|>",
        "<MID>",
        "<|fim_middle|>",
    ]
    lang = str(language or "").lower()
    if lang in {"systemverilog", "verilog", "sv"}:
        universal.extend(
            [
                "\nendmodule",
                "\nmodule ",
                "\ninterface ",
                "\npackage ",
                "\nprogram ",
                "\nclass ",
                "\nalways",
                "\nalways_ff",
                "\nalways_comb",
                "\ninitial",
                "\nfunction",
                "\ntask",
                "\ngenerate",
                "\n`include",
                "\n`define",
            ]
        )
    return universal


@dataclass
class StopCondition:
    stop_sequences: list[str]
    _reversed: list[str] = field(init=False, repr=False)
    _accumulated: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        self._reversed = sorted(
            (seq[::-1] for seq in self.stop_sequences if seq),
            key=len,
            reverse=True,
        )

    def should_stop(self, new_text: str) -> tuple[bool, int]:
        if not new_text:
            return False, 0
        self._accumulated += new_text
        rev = self._accumulated[::-1]
        for seq in self._reversed:
            if rev.startswith(seq):
                return True, len(seq)
        return False, 0

    def apply(self, text: str) -> str:
        """Apply stop trimming to a complete string."""
        condition = StopCondition(self.stop_sequences)
        for i in range(len(text)):
            chunk = text[i : i + 1]
            should_stop, stop_len = condition.should_stop(chunk)
            if should_stop:
                trim = len(condition._accumulated) - stop_len
                return condition._accumulated[: max(0, trim)]
        return text


def create_stop_condition(language: str | None) -> StopCondition:
    return StopCondition(_default_stop_sequences(language))
