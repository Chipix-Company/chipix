"""
Unified AI client for local runtime with optional legacy provider compatibility.

Default production path uses an OpenAI-compatible local endpoint (llama.cpp server).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from urllib.parse import quote
from typing import Optional

import aiohttp

try:
    import config
except ModuleNotFoundError:
    from original_core import config

try:
    from core.logger import get_logger
except ModuleNotFoundError:
    from original_core.core.logger import get_logger

logger = get_logger("AIClient")


class AIClientError(Exception):
    """Raised when an API call fails."""


class AIClient:
    """Single client abstraction used by all pipeline agents."""

    def __init__(self) -> None:
        provider = (config.LLM_PROVIDER or "gemini").strip().lower()
        if provider not in {"local", "gemini", "nim", "openai", "azure_openai", "bedrock", "convex_gateway", "demo"}:
            logger.warning(
                "Unsupported CHIPVERIFY_LLM_PROVIDER '%s'; falling back to gemini.",
                provider,
            )
            provider = "gemini"

        self.provider = provider
        self._azure_token_provider = None
        self.allow_local_provider = bool(config.ALLOW_LOCAL_LLM)
        self.base_url = (config.LLM_BASE_URL or "").rstrip("/")
        self.model_alias = (
            config.GEMINI_MODEL if self.provider == "gemini" else config.LLM_MODEL_ALIAS
        )
        self.timeout_seconds = config.LLM_TIMEOUT_SECONDS
        self.azure_use_aad = bool(getattr(config, "AZURE_OPENAI_USE_AAD", False))
        self.api_key_required = bool(
            config.LLM_API_KEY_REQUIRED and not self.azure_use_aad
        )

        # `api_key` is kept for backward compatibility with existing callers.
        if self.provider == "gemini":
            self.api_key = config.GEMINI_API_KEY
            if not self.api_key:
                logger.warning(
                    "GEMINI_API_KEY is not set while CHIPVERIFY_LLM_PROVIDER=gemini."
                )
        else:
            self.api_key = config.LLM_API_KEY
            if not self.base_url:
                logger.warning(
                    "CHIPVERIFY_LLM_BASE_URL is empty while CHIPVERIFY_LLM_PROVIDER=%s.",
                    self.provider,
                )
            if self.api_key_required and not self.api_key:
                logger.warning(
                    "CHIPVERIFY_LLM_API_KEY is required by policy (CHIPVERIFY_LLM_API_KEY_REQUIRED=true) but missing."
                )
            if self.provider == "azure_openai" and self.azure_use_aad:
                logger.info(
                    "Azure OpenAI provider will use DefaultAzureCredential with scope %s.",
                    getattr(config, "AZURE_OPENAI_TOKEN_SCOPE", ""),
                )

    def is_generation_configured(self) -> bool:
        if self.provider == "demo":
            return True
        if self.provider == "local" and not self.allow_local_provider:
            return False

        if self.provider == "gemini":
            return bool(self.api_key)

        if self.api_key_required:
            return bool(self.base_url and self.model_alias and self.api_key)

        return bool(self.base_url and self.model_alias)

    def _openai_token_limit_key(self) -> str:
        """GPT-5/o-series chat models reject legacy max_tokens.

        Azure AI/OpenAI-compatible endpoints use the same parameter contract
        for these deployments, so azure_openai must follow the modern key too.
        """
        model = str(self.model_alias or "").strip().lower()
        if self.provider in {"openai", "azure_openai"} and model.startswith(
            ("gpt-5", "o1", "o3", "o4")
        ):
            return "max_completion_tokens"
        return "max_tokens"

    def _openai_uses_modern_chat_limits(self) -> bool:
        return self._openai_token_limit_key() == "max_completion_tokens"

    def _should_request_openai_json_object(
        self,
        messages: list[dict[str, str]],
    ) -> bool:
        """Use JSON mode for structured mental-model style prompts.

        Azure AI Foundry / Azure OpenAI `/openai/v1` endpoints expose the
        OpenAI-compatible chat-completions contract, so they should get the
        same `response_format={"type": "json_object"}` treatment as OpenAI.
        Legacy Azure deployment URLs keep the older compatibility behavior and
        rely on the existing retry-without-response_format fallback.
        """
        if self.provider not in {"openai", "azure_openai"}:
            return False
        if self.provider == "azure_openai" and self._use_azure_deployment_api():
            return False
        combined = "\n".join(str(m.get("content") or "") for m in messages).lower()
        return "json" in combined and (
            "json only" in combined
            or "valid json" in combined
            or "return exactly one json object" in combined
            or "return one json object" in combined
        )

    @staticmethod
    def _is_response_format_unsupported(status: int, body: str) -> bool:
        if status not in {400, 404, 422}:
            return False
        lowered = str(body or "").lower()
        return "response_format" in lowered or "json_object" in lowered

    async def is_runtime_ready(self) -> bool:
        """Probe runtime health when running in local provider mode."""
        if self.provider == "local" and not self.allow_local_provider:
            return False

        if self.provider != "local":
            return self.is_generation_configured()

        if not self.is_generation_configured():
            return False

        timeout = aiohttp.ClientTimeout(total=min(5, max(1, self.timeout_seconds)))
        for health_url in self._health_urls():
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(health_url) as resp:
                        if resp.status != 200:
                            continue
                        payload = await resp.json(content_type=None)
                        if isinstance(payload, dict) and payload.get("status") == "ok":
                            return True
            except Exception:
                continue

        return False

    # ------------------------------------------------------------------
    # Main API call
    # ------------------------------------------------------------------
    async def generate_with_messages(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = config.DEFAULT_TEMPERATURE,
        max_tokens: int = config.DEFAULT_MAX_TOKENS,
    ) -> str:
        if self.provider == "local" and not self.allow_local_provider:
            raise AIClientError(
                "local_provider_disabled: local model runtime is disabled by policy; use gemini or nim provider"
            )

        if self.provider == "gemini":
            return await self._generate_gemini_with_messages(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        if self.provider in {"bedrock", "convex_gateway", "demo"}:
            import llm_provider

            response = await asyncio.to_thread(
                llm_provider.chat_with_tools,
                messages,
                [],
                self.model_alias,
                temperature,
                max_tokens,
                self.provider,
            )
            return response.content

        if self.provider in {"nim", "openai", "azure_openai"}:
            return await self._generate_openai_compatible_with_messages(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        return await self._generate_local_with_messages(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float = config.DEFAULT_TEMPERATURE,
        max_tokens: int = config.DEFAULT_MAX_TOKENS,
    ) -> str:
        if self.provider == "local" and not self.allow_local_provider:
            raise AIClientError(
                "local_provider_disabled: local model runtime is disabled by policy; use gemini or nim provider"
            )

        if self.provider == "gemini":
            return await self._generate_gemini(
                prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        if self.provider in {"bedrock", "convex_gateway", "demo"}:
            import llm_provider

            content, _reasoning = await asyncio.to_thread(
                llm_provider.generate,
                system_prompt,
                prompt,
                self.model_alias,
                temperature,
                max_tokens,
                self.provider,
            )
            return content

        if self.provider in {"nim", "openai", "azure_openai"}:
            messages: list[dict[str, str]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            return await self._generate_openai_compatible_with_messages(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        return await self._generate_local(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def _generate_openai_compatible_with_messages(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if self.api_key_required and not self.api_key:
            raise AIClientError(
                "provider_auth_failed: API key is required by policy (CHIPVERIFY_LLM_API_KEY_REQUIRED=true)"
            )

        if not self.is_generation_configured():
            raise AIClientError("provider_unreachable: provider config is incomplete")

        headers = self._openai_compatible_headers()

        requested_max_tokens = max(1, int(max_tokens))
        prompt_text = "\n".join(m.get("content", "") for m in messages)
        prompt_tokens_estimate = self._estimate_tokens(prompt_text)
        effective_max_tokens = self._compute_safe_max_tokens(
            prompt_tokens_estimate,
            requested_max_tokens,
        )

        payload = {
            "messages": messages,
        }
        if not self._openai_uses_modern_chat_limits():
            payload["temperature"] = temperature
        payload[self._openai_token_limit_key()] = effective_max_tokens
        if not self._use_azure_deployment_api():
            payload["model"] = self.model_alias
        if self._should_request_openai_json_object(messages):
            payload["response_format"] = {"type": "json_object"}

        url = self._openai_compatible_chat_url()
        logger.debug(
            "provider request: provider=%s model=%s prompt_len=%d max_tokens=%d",
            self.provider,
            self.model_alias,
            len(prompt_text),
            effective_max_tokens,
        )

        try:
            async with aiohttp.ClientSession() as session:
                result = None
                for attempt in range(2):
                    async with session.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
                    ) as resp:
                        body = await resp.text()
                        if resp.status == 200:
                            result = json.loads(body)
                            break
                        if (
                            attempt == 0
                            and "response_format" in payload
                            and self._is_response_format_unsupported(resp.status, body)
                        ):
                            logger.warning(
                                "OpenAI JSON response_format unsupported by %s; retrying without it.",
                                self.model_alias,
                            )
                            payload.pop("response_format", None)
                            continue
                        error_code, error_message = self._map_local_runtime_error(
                            resp.status,
                            body,
                        )
                        raise AIClientError(f"{error_code}: {error_message}")
                if result is None:
                    raise AIClientError("provider_unreachable: provider returned no response")
        except asyncio.TimeoutError as exc:
            raise AIClientError(
                "provider_timeout: request timed out while waiting for model response"
            ) from exc
        except aiohttp.ClientConnectorError as exc:
            raise AIClientError(
                "provider_unreachable: could not connect to model provider"
            ) from exc
        except aiohttp.ClientError as exc:
            raise AIClientError(
                f"provider_unreachable: provider request failed: {exc}"
            ) from exc

        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIClientError(
                f"provider_unreachable: unexpected response structure: {json.dumps(result)[:800]}"
            ) from exc

        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
            text = "\n".join(part for part in text_parts if part)
        else:
            text = ""

        if not text.strip():
            raise AIClientError("provider_unreachable: runtime returned empty text")

        return text

    async def _generate_local_with_messages(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if self.api_key_required and not self.api_key:
            raise AIClientError(
                "local_runtime_auth_failed: CHIPVERIFY_LLM_API_KEY is required by policy (CHIPVERIFY_LLM_API_KEY_REQUIRED=true)"
            )

        if not self.is_generation_configured():
            raise AIClientError(
                "local_runtime_unreachable: local runtime config is incomplete"
            )

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        requested_max_tokens = max(1, int(max_tokens))
        prompt_text = "\n".join(m.get("content", "") for m in messages)
        prompt_tokens_estimate = self._estimate_tokens(prompt_text)
        effective_max_tokens = self._compute_safe_max_tokens(
            prompt_tokens_estimate,
            requested_max_tokens,
        )

        payload = {
            "model": self.model_alias,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": effective_max_tokens,
        }

        url = f"{self.base_url}/chat/completions"
        logger.debug(
            "local runtime request: model=%s prompt_len=%d max_tokens=%d",
            self.model_alias,
            len(prompt_text),
            effective_max_tokens,
        )
        start_time = time.time()

        result = None
        attempt = 0
        while attempt < 2:
            attempt += 1
            payload["max_tokens"] = effective_max_tokens
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
                    ) as resp:
                        body = await resp.text()
                        if resp.status != 200:
                            error_code, error_message = self._map_local_runtime_error(
                                resp.status,
                                body,
                            )

                            if (
                                error_code == "local_runtime_context_overflow"
                                and attempt == 1
                            ):
                                adjusted_max_tokens = (
                                    self._adjust_max_tokens_after_overflow(
                                        error_message,
                                        prompt_tokens_estimate,
                                        effective_max_tokens,
                                    )
                                )
                                if adjusted_max_tokens < effective_max_tokens:
                                    logger.warning(
                                        "context overflow detected; retrying with max_tokens=%d (was %d)",
                                        adjusted_max_tokens,
                                        effective_max_tokens,
                                    )
                                    effective_max_tokens = adjusted_max_tokens
                                    continue

                            logger.error(
                                "local runtime error %s (%s): %s",
                                resp.status,
                                error_code,
                                error_message,
                            )
                            raise AIClientError(f"{error_code}: {error_message}")

                        result = json.loads(body)
                        break
            except asyncio.TimeoutError as exc:
                raise AIClientError(
                    "local_runtime_timeout: request timed out while waiting for model response"
                ) from exc
            except aiohttp.ClientConnectorError as exc:
                raise AIClientError(
                    "local_runtime_unreachable: could not connect to local model runtime"
                ) from exc
            except aiohttp.ClientError as exc:
                raise AIClientError(
                    f"local_runtime_unreachable: local runtime request failed: {exc}"
                ) from exc

        if result is None:
            raise AIClientError(
                "local_runtime_unreachable: failed to get response from local runtime"
            )

        elapsed = time.time() - start_time

        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIClientError(
                f"local_runtime_unreachable: unexpected response structure: {json.dumps(result)[:800]}"
            ) from exc

        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
            text = "\n".join(part for part in text_parts if part)
        else:
            text = ""

        if not text.strip():
            logger.warning(
                "Local runtime returned empty text. This often happens with small models (like 1.5B-7B) when system prompts are too complex or chat templates are mismatched."
            )
            raise AIClientError(
                "local_runtime_unreachable: runtime returned empty text (model likely collapsed due to prompt complexity)"
            )

        logger.debug("local runtime response: %d chars in %.1fs", len(text), elapsed)
        return text

    async def _generate_local(
        self,
        prompt: str,
        *,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if self.api_key_required and not self.api_key:
            raise AIClientError(
                "local_runtime_auth_failed: CHIPVERIFY_LLM_API_KEY is required by policy (CHIPVERIFY_LLM_API_KEY_REQUIRED=true)"
            )

        if not self.is_generation_configured():
            raise AIClientError(
                "local_runtime_unreachable: local runtime config is incomplete"
            )

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        requested_max_tokens = max(1, int(max_tokens))
        prompt_tokens_estimate = self._estimate_tokens(
            system_prompt
        ) + self._estimate_tokens(prompt)
        effective_max_tokens = self._compute_safe_max_tokens(
            prompt_tokens_estimate,
            requested_max_tokens,
        )

        payload = {
            "model": self.model_alias,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": effective_max_tokens,
        }

        url = f"{self.base_url}/chat/completions"
        logger.debug(
            "local runtime request: model=%s prompt_len=%d max_tokens=%d",
            self.model_alias,
            len(prompt),
            effective_max_tokens,
        )
        start_time = time.time()

        result = None
        attempt = 0
        while attempt < 2:
            attempt += 1
            payload["max_tokens"] = effective_max_tokens
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
                    ) as resp:
                        body = await resp.text()
                        if resp.status != 200:
                            error_code, error_message = self._map_local_runtime_error(
                                resp.status,
                                body,
                            )

                            if (
                                error_code == "local_runtime_context_overflow"
                                and attempt == 1
                            ):
                                adjusted_max_tokens = (
                                    self._adjust_max_tokens_after_overflow(
                                        error_message,
                                        prompt_tokens_estimate,
                                        effective_max_tokens,
                                    )
                                )
                                if adjusted_max_tokens < effective_max_tokens:
                                    logger.warning(
                                        "context overflow detected; retrying with max_tokens=%d (was %d)",
                                        adjusted_max_tokens,
                                        effective_max_tokens,
                                    )
                                    effective_max_tokens = adjusted_max_tokens
                                    continue

                            logger.error(
                                "local runtime error %s (%s): %s",
                                resp.status,
                                error_code,
                                error_message,
                            )
                            raise AIClientError(f"{error_code}: {error_message}")

                        result = json.loads(body)
                        break
            except asyncio.TimeoutError as exc:
                raise AIClientError(
                    "local_runtime_timeout: request timed out while waiting for model response"
                ) from exc
            except aiohttp.ClientConnectorError as exc:
                raise AIClientError(
                    "local_runtime_unreachable: could not connect to local model runtime"
                ) from exc
            except aiohttp.ClientError as exc:
                raise AIClientError(
                    f"local_runtime_unreachable: local runtime request failed: {exc}"
                ) from exc

        if result is None:
            raise AIClientError(
                "local_runtime_unreachable: failed to get response from local runtime"
            )

        elapsed = time.time() - start_time

        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIClientError(
                f"local_runtime_unreachable: unexpected response structure: {json.dumps(result)[:800]}"
            ) from exc

        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
            text = "\n".join(part for part in text_parts if part)
        else:
            text = ""

        if not text.strip():
            # For small models, sometimes the system prompt is too long/complex
            # causing immediate generation collapse. We raise a specific hint.
            logger.warning(
                "Local runtime returned empty text. This often happens with small models (like 1.5B-7B) when system prompts are too complex or chat templates are mismatched."
            )
            raise AIClientError(
                "local_runtime_unreachable: runtime returned empty text (model likely collapsed due to prompt complexity)"
            )

        logger.debug("local runtime response: %d chars in %.1fs", len(text), elapsed)
        return text

    async def _generate_gemini(
        self,
        prompt: str,
        *,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        return await self._generate_gemini_with_messages(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def _generate_gemini_with_messages(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if not self.api_key:
            raise AIClientError("GEMINI_API_KEY is not set in the environment.")

        headers = {"Content-Type": "application/json"}

        system_content = ""
        contents = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system_content = content
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})

        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }

        if system_content:
            payload["system_instruction"] = {"parts": [{"text": system_content}]}

        gemini_model = (self.model_alias or config.GEMINI_MODEL).strip()
        if not gemini_model:
            gemini_model = "gemini-2.5-flash"
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{gemini_model}:generateContent?key={self.api_key}"
        )

        full_prompt_text = (
            system_content + "\n" + "\n".join(m.get("content", "") for m in messages)
        )
        logger.info(
            "gemini request: model=%s messages=%d prompt_len=%d",
            gemini_model,
            len(messages),
            len(full_prompt_text),
        )
        logger.info(
            "gemini request URL: %s",
            url.replace(self.api_key, "***") if self.api_key else url,
        )
        start_time = time.time()

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=config.API_TIMEOUT_SECONDS),
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    logger.error("Gemini API error %s: %s", resp.status, body[:500])
                    raise AIClientError(
                        f"Gemini API returned {resp.status}: {body[:500]}"
                    )
                result = json.loads(body)

        elapsed = time.time() - start_time

        try:
            candidate = result["candidates"][0]
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
        except (KeyError, IndexError, TypeError):
            logger.warning(
                "Gemini returned a non-text response; using recovery guidance. payload=%s",
                json.dumps(result)[:800],
            )
            return "Continue with the task using available context and tool results."

        function_call_blocks = []
        for part in parts:
            fn = part.get("functionCall") if isinstance(part, dict) else None
            if not isinstance(fn, dict):
                continue
            fn_name = str(fn.get("name") or "").strip()
            fn_args = fn.get("args")
            if not fn_name:
                continue
            if not isinstance(fn_args, dict):
                fn_args = {}
            function_call_blocks.append(
                "```json\n"
                + json.dumps({"tool": fn_name, "args": fn_args}, ensure_ascii=False)
                + "\n```"
            )

        if function_call_blocks:
            return "\n\n".join(function_call_blocks)

        text_parts = [
            p["text"] for p in parts if "text" in p and not p.get("thought", False)
        ]

        if not text_parts:
            text_parts = [p["text"] for p in parts if "text" in p]

        if not text_parts:
            logger.warning(
                "Gemini returned no text parts; using recovery guidance. parts=%s",
                json.dumps(parts)[:400],
            )
            return "Continue with the task using available context and tool results."

        text = "\n".join(text_parts)
        logger.info("gemini response: %d chars in %.1fs", len(text), elapsed)
        return text

    def _health_urls(self) -> list[str]:
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            root = base[:-3]
            return [f"{root}/health", f"{base}/health"]

        return [f"{base}/health", f"{base}/v1/health"]

    def _use_azure_deployment_api(self) -> bool:
        return self.provider == "azure_openai" and getattr(
            config, "AZURE_OPENAI_API_STYLE", "deployment"
        ) in {"deployment", "azure_deployment", "legacy"}

    def _azure_resource_root(self) -> str:
        base = self.base_url.rstrip("/")
        for suffix in ("/openai/v1", "/openai"):
            if base.lower().endswith(suffix):
                return base[: -len(suffix)].rstrip("/")
        return base

    def _openai_compatible_chat_url(self) -> str:
        if not self._use_azure_deployment_api():
            return f"{self.base_url}/chat/completions"

        deployment = quote(self.model_alias, safe="")
        api_version = getattr(config, "AZURE_OPENAI_API_VERSION", "2024-10-21")
        return (
            f"{self._azure_resource_root()}/openai/deployments/"
            f"{deployment}/chat/completions?api-version={api_version}"
        )

    def _openai_compatible_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}

        if self.provider == "azure_openai" and self.azure_use_aad:
            headers["Authorization"] = f"Bearer {self._get_azure_ad_token()}"
            return headers

        if not self.api_key:
            return headers

        if self.provider == "azure_openai":
            auth_header = getattr(config, "AZURE_OPENAI_AUTH_HEADER", "bearer")
            auth_header = str(auth_header or "bearer").strip().lower()
            if auth_header in {"api-key", "apikey", "api_key"}:
                headers["api-key"] = self.api_key
            elif auth_header == "both":
                headers["api-key"] = self.api_key
                headers["Authorization"] = f"Bearer {self.api_key}"
            else:
                headers["Authorization"] = f"Bearer {self.api_key}"
            return headers

        headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _get_azure_ad_token(self) -> str:
        try:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        except ImportError as exc:
            raise AIClientError(
                "provider_auth_failed: azure-identity is required when AZURE_OPENAI_USE_AAD=true"
            ) from exc

        if self._azure_token_provider is None:
            self._azure_token_provider = get_bearer_token_provider(
                DefaultAzureCredential(),
                getattr(config, "AZURE_OPENAI_TOKEN_SCOPE", "https://ai.azure.com/.default"),
            )

        try:
            token = self._azure_token_provider()
        except Exception as exc:
            raise AIClientError(
                f"provider_auth_failed: Azure credential token acquisition failed: {exc}"
            ) from exc

        if not token:
            raise AIClientError("provider_auth_failed: Azure credential returned no token")
        return token

    def _map_local_runtime_error(self, status_code: int, body: str) -> tuple[str, str]:
        message, error_type = self._extract_openai_error(body)
        combined = f"{message} {error_type}".lower()

        if "exceeds the available context size" in combined:
            return "local_runtime_context_overflow", message

        if status_code in {401, 403}:
            return "local_runtime_auth_failed", message

        if status_code == 503 and "loading" in combined:
            return "local_runtime_loading", message

        if "model" in combined and ("not found" in combined or "unknown" in combined):
            return "local_runtime_model_not_found", message

        if status_code in {408, 504} or "timeout" in combined:
            return "local_runtime_timeout", message

        return "local_runtime_unreachable", message

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        # Conservative approximation for code-heavy prompts.
        return max(1, len(text) // 2)

    @staticmethod
    def _clip_text(text: str, max_chars: int) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text

        marker = "\n...[truncated for context budget]...\n"
        if max_chars <= len(marker) + 16:
            return text[:max_chars]

        head_chars = (max_chars - len(marker)) // 2
        tail_chars = max_chars - len(marker) - head_chars
        return f"{text[:head_chars]}{marker}{text[-tail_chars:]}"

    def _runtime_context_tokens(self) -> int:
        hard_cap_raw = os.getenv("CHIPVERIFY_LLM_CONTEXT_HARD_CAP", "4096")
        raw_value = os.getenv("CHIPVERIFY_LLM_CONTEXT_SIZE", hard_cap_raw)
        try:
            configured = int(raw_value)
            hard_cap = int(hard_cap_raw)
            return max(1024, min(configured, hard_cap))
        except ValueError:
            return 4096

    def _compute_safe_max_tokens(
        self,
        prompt_tokens_estimate: int,
        requested_max_tokens: int,
    ) -> int:
        context_tokens = self._runtime_context_tokens()
        safety_headroom = 192
        available_for_output = context_tokens - prompt_tokens_estimate - safety_headroom

        if available_for_output <= 64:
            return 64

        return max(64, min(requested_max_tokens, available_for_output))

    def _adjust_max_tokens_after_overflow(
        self,
        error_message: str,
        prompt_tokens_estimate: int,
        current_max_tokens: int,
    ) -> int:
        available_context = self._runtime_context_tokens()
        match = re.search(
            r"available context size \((\d+) tokens\)",
            error_message,
            re.IGNORECASE,
        )
        if match:
            available_context = max(1024, int(match.group(1)))

        safety_headroom = 192
        remaining = available_context - prompt_tokens_estimate - safety_headroom
        conservative_cap = max(64, current_max_tokens // 2)
        return max(64, min(conservative_cap, remaining))

    @staticmethod
    def _extract_openai_error(body: str) -> tuple[str, str]:
        fallback = (body or "Runtime request failed")[:500]
        try:
            payload = json.loads(body)
        except Exception:
            return fallback, ""

        if not isinstance(payload, dict):
            return fallback, ""

        error_payload = payload.get("error")
        if not isinstance(error_payload, dict):
            return fallback, ""

        message = str(error_payload.get("message") or fallback)[:500]
        error_type = str(error_payload.get("type") or "")
        return message, error_type

    # ------------------------------------------------------------------
    # Convenience: generate with structured context
    # ------------------------------------------------------------------
    async def generate_with_context(
        self,
        system_prompt: str,
        context_blocks: dict[str, str],
        focus_instruction: str,
        task_instruction: str,
        *,
        temperature: float = 0.5,
    ) -> str:
        """Build a structured prompt from context blocks and call active provider.

        This is the primary method used by the File Generator.
        """

        max_total_chars = max(1000, getattr(config, "PROMPT_MAX_CHARS", 8000))
        max_block_chars = max(300, getattr(config, "PROMPT_BLOCK_MAX_CHARS", 3200))

        # Build the structured user prompt
        sections = []

        # Focus instruction at the top
        if focus_instruction:
            focus_text = self._clip_text(focus_instruction, 1200)
            sections.append(
                f"┌─── FOCUS INSTRUCTION ───────────────────────────────────────┐\n"
                f"{focus_text}\n"
                f"└─────────────────────────────────────────────────────────────┘"
            )

        # Context blocks with clear labels
        remaining_chars = max_total_chars
        for label, content in context_blocks.items():
            if remaining_chars <= 0:
                break

            allowed_chars = min(max_block_chars, remaining_chars)
            compact_content = self._clip_text(content, allowed_chars)
            sections.append(
                f"═══ {label} ═══════════════════════════════════════════════════\n"
                f"{compact_content}"
            )
            remaining_chars -= len(compact_content)

        # Task instruction at the end
        task_text = self._clip_text(task_instruction, 1200)
        sections.append(
            f"═══ YOUR TASK ═════════════════════════════════════════════════\n"
            f"{task_text}"
        )

        user_prompt = "\n\n".join(sections)
        user_prompt = self._clip_text(user_prompt, max_total_chars + 400)

        return await self.generate(
            user_prompt,
            system_prompt=system_prompt,
            temperature=temperature,
        )
