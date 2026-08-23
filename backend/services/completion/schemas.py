"""Pydantic models for tab-completion API (Tabby-compatible + Chip-Verify extensions)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Snippet(BaseModel):
    filepath: str = ""
    body: str = ""
    kind: Optional[str] = None
    score: Optional[float] = None
    source: Optional[str] = None


class DebugOptions(BaseModel):
    return_prompt: bool = False
    return_snippets: bool = False
    disable_rag: bool = False
    raw_prompt: Optional[str] = None


class Segments(BaseModel):
    prefix: str = ""
    suffix: str = ""
    filepath: Optional[str] = None
    language: Optional[str] = "systemverilog"
    project_id: Optional[str] = None
    target_module: Optional[str] = None
    rtl_artifact_id: Optional[str] = None
    spec_artifact_id: Optional[str] = None
    mental_model_revision_id: Optional[str] = None
    declarations: list[Snippet] = Field(default_factory=list)
    relevant_snippets_from_changed_files: list[Snippet] = Field(default_factory=list)
    relevant_snippets_from_recently_opened_files: list[Snippet] = Field(default_factory=list)
    relevant_snippets_from_spec: list[Snippet] = Field(default_factory=list)
    relevant_snippets_from_mental_model: list[Snippet] = Field(default_factory=list)
    cursor_line: Optional[int] = None
    cursor_column: Optional[int] = None
    clipboard: Optional[str] = None


class CompletionRequest(BaseModel):
    language: Optional[str] = "systemverilog"
    segments: Segments
    user: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    seed: Optional[int] = None
    mode: str = "standard"
    stream: bool = False
    debug_options: Optional[DebugOptions] = None


class CompletionChoice(BaseModel):
    index: int = 0
    text: str = ""


class CompletionResponse(BaseModel):
    id: str
    choices: list[CompletionChoice]
    mode: str = "standard"
    latency_ms: Optional[int] = None
    debug_data: Optional[dict[str, Any]] = None


class CompletionEventRequest(BaseModel):
    type: Literal["view", "select", "dismiss"]
    completion_id: str
    choice_index: Optional[int] = 0
    choice_text: Optional[str] = None
    select_kind: Optional[str] = None
    elapsed_ms: Optional[int] = None
    filepath: Optional[str] = None
    language: Optional[str] = None
    segments: Optional[dict[str, Any]] = None
