"""Core completion orchestration service."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from database.models import Project
from services.completion.config import CompletionConfig, load_completion_config
from services.completion.events import log_completion_event
from services.completion.prompt_builder import PromptBuilder, normalize_line_endings
from services.completion.rate_limit import CompletionRateLimiter
from services.completion.schemas import (
    CompletionChoice,
    CompletionRequest,
    CompletionResponse,
)
from services.completion.snippet_collector import SnippetCollector
from services.completion.stop_conditions import create_stop_condition
from services.token_usage import estimate_text_token_usage, persist_project_token_usage

logger = logging.getLogger(__name__)

_rate_limiter = CompletionRateLimiter(
    max_per_minute=load_completion_config().rate_limit_per_min
)


class CompletionError(Exception):
    pass


class CompletionService:
    def __init__(self, config: Optional[CompletionConfig] = None) -> None:
        self.config = config or load_completion_config()
        self.prompt_builder = PromptBuilder(self.config)
        self.snippet_collector = SnippetCollector(self.config)

    def generate(
        self,
        db: Session,
        project: Project,
        request: CompletionRequest,
        *,
        user_id: str,
        user_agent: Optional[str] = None,
    ) -> CompletionResponse:
        if not self.config.enabled:
            raise CompletionError("Completion service is disabled")

        if not _rate_limiter.allow(user_id):
            raise CompletionError("Rate limit exceeded")

        segments = request.segments
        if segments.project_id and segments.project_id != project.id:
            raise CompletionError("Project mismatch in segments")

        debug = request.debug_options
        disable_rag = True if debug is None else bool(debug.disable_rag)

        prefix = normalize_line_endings(segments.prefix)
        suffix = normalize_line_endings(segments.suffix or "")
        if not prefix and not suffix and not (debug and debug.raw_prompt):
            raise CompletionError("Empty prompt")

        client_snippets = self.prompt_builder.collect_client_snippets(segments)
        snippet_blocks, budget_remaining = self.prompt_builder.merge_snippets(
            client_snippets
        )

        server_snippets = self.snippet_collector.collect_server_snippets(
            db,
            project,
            segments,
            budget_remaining=budget_remaining,
            disable_rag=disable_rag,
        )
        if server_snippets:
            extra_blocks, budget_remaining = self.prompt_builder.merge_snippets(
                server_snippets,
                budget=budget_remaining,
            )
            snippet_blocks.extend(extra_blocks)

        raw_prompt = debug.raw_prompt if debug else None
        build_result = self.prompt_builder.build(
            segments,
            snippet_blocks,
            raw_prompt=raw_prompt,
        )

        temperature = (
            request.temperature
            if request.temperature is not None
            else self.config.temperature
        )
        max_tokens = (
            request.max_tokens
            if request.max_tokens is not None
            else self.config.max_output_tokens
        )

        from llm_provider import complete_fim_sync

        started = time.perf_counter()
        generated_text, usage = complete_fim_sync(
            build_result.prompt,
            model=self.config.model,
            provider=self.config.provider,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_sec=self.config.timeout_sec,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        stop = create_stop_condition(request.language or segments.language)
        trimmed = stop.apply(generated_text)

        completion_id = f"cmpl-{uuid.uuid4()}"

        if usage:
            persist_project_token_usage(
                db,
                project_id=project.id,
                user_id=user_id,
                usage=usage,
                source="completion",
                metadata={"completion_id": completion_id},
            )
        else:
            estimated = estimate_text_token_usage(
                input_text=build_result.prompt,
                output_text=trimmed,
                provider=self.config.provider,
                model=self.config.model,
                details={"source": "completion_estimate"},
            )
            persist_project_token_usage(
                db,
                project_id=project.id,
                user_id=user_id,
                usage=estimated,
                source="completion",
                metadata={"completion_id": completion_id, "is_estimated": True},
            )

        log_completion_event(
            db,
            event_type="completion",
            completion_id=completion_id,
            user_id=user_id,
            project_id=project.id,
            language=request.language or segments.language,
            filepath=segments.filepath,
            segments={"prefix": prefix, "suffix": suffix, "filepath": segments.filepath},
            model=self.config.model,
            latency_ms=latency_ms,
            user_agent=user_agent,
        )
        db.commit()

        debug_data: dict[str, Any] | None = None
        if debug and (debug.return_prompt or debug.return_snippets):
            debug_data = {}
            if debug.return_prompt:
                debug_data["prompt"] = build_result.prompt
            if debug.return_snippets:
                debug_data["snippets"] = build_result.snippets_used

        return CompletionResponse(
            id=completion_id,
            choices=[CompletionChoice(index=0, text=trimmed)],
            mode=request.mode,
            latency_ms=latency_ms,
            debug_data=debug_data,
        )

    async def generate_stream(
        self,
        db: Session,
        project: Project,
        request: CompletionRequest,
        *,
        user_id: str,
        user_agent: Optional[str] = None,
    ):
        """SSE-friendly incremental completion stream."""
        if not self.config.enabled:
            raise CompletionError("Completion service is disabled")
        if not _rate_limiter.allow(user_id):
            raise CompletionError("Rate limit exceeded")

        segments = request.segments
        if segments.project_id and segments.project_id != project.id:
            raise CompletionError("Project mismatch in segments")

        debug = request.debug_options
        disable_rag = True if debug is None else bool(debug.disable_rag)
        prefix = normalize_line_endings(segments.prefix)
        suffix = normalize_line_endings(segments.suffix or "")
        if not prefix and not suffix and not (debug and debug.raw_prompt):
            raise CompletionError("Empty prompt")

        client_snippets = self.prompt_builder.collect_client_snippets(segments)
        snippet_blocks, budget_remaining = self.prompt_builder.merge_snippets(
            client_snippets
        )
        server_snippets = self.snippet_collector.collect_server_snippets(
            db,
            project,
            segments,
            budget_remaining=budget_remaining,
            disable_rag=disable_rag,
        )
        if server_snippets:
            extra_blocks, budget_remaining = self.prompt_builder.merge_snippets(
                server_snippets,
                budget=budget_remaining,
            )
            snippet_blocks.extend(extra_blocks)

        raw_prompt = debug.raw_prompt if debug else None
        build_result = self.prompt_builder.build(
            segments,
            snippet_blocks,
            raw_prompt=raw_prompt,
        )

        temperature = (
            request.temperature
            if request.temperature is not None
            else self.config.temperature
        )
        max_tokens = (
            request.max_tokens
            if request.max_tokens is not None
            else self.config.max_output_tokens
        )

        from llm_provider import complete_fim_stream

        started = time.perf_counter()
        completion_id = f"cmpl-{uuid.uuid4()}"
        stop = create_stop_condition(request.language or segments.language)
        accumulated = ""

        for chunk in complete_fim_stream(
            build_result.prompt,
            model=self.config.model,
            provider=self.config.provider,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_sec=self.config.timeout_sec,
        ):
            should_stop, _stop_len = stop.should_stop(chunk)
            if should_stop:
                accumulated = stop.apply(accumulated + chunk)
                break
            accumulated += chunk
            yield {
                "id": completion_id,
                "choices": [{"index": 0, "text": accumulated}],
                "mode": request.mode,
            }

        trimmed = stop.apply(accumulated) if accumulated else ""
        latency_ms = int((time.perf_counter() - started) * 1000)

        estimated = estimate_text_token_usage(
            input_text=build_result.prompt,
            output_text=trimmed,
            provider=self.config.provider,
            model=self.config.model,
            details={"source": "completion_stream_estimate"},
        )
        persist_project_token_usage(
            db,
            project_id=project.id,
            user_id=user_id,
            usage=estimated,
            source="completion",
            metadata={"completion_id": completion_id, "is_estimated": True},
        )
        log_completion_event(
            db,
            event_type="completion",
            completion_id=completion_id,
            user_id=user_id,
            project_id=project.id,
            language=request.language or segments.language,
            filepath=segments.filepath,
            segments={"prefix": prefix, "suffix": suffix, "filepath": segments.filepath},
            model=self.config.model,
            latency_ms=latency_ms,
            user_agent=user_agent,
        )
        db.commit()

        yield {
            "id": completion_id,
            "choices": [{"index": 0, "text": trimmed}],
            "mode": request.mode,
            "done": True,
        }


def get_completion_service() -> CompletionService:
    return CompletionService()
