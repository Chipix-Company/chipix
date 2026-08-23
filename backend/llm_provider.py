"""
LLM Provider Abstraction Layer
===============================
Unified interface for multiple LLM providers:
    - Gemini (google-genai)
    - OpenAI SDK (official OpenAI API)
    - Amazon Bedrock (OpenAI-compatible chat completions)
    - NVIDIA NIM (OpenAI-compatible)
    - Convex cloud gateway (server-side provider keys, quota, config)
    - Any OpenAI-compatible endpoint (local llama.cpp, Ollama, etc.)

Configuration via environment variables:
  CHIPVERIFY_LLM_PROVIDER / MODEL_PROVIDER
                     = "gemini" | "bedrock" | "openai" | "nim" | "azure_openai"
                       | "convex_gateway"
                       (default: "bedrock" when BEDROCK_API_KEY is set, else "gemini")
  GOOGLE_API_KEY     = Gemini API key
  OPENAI_API_KEY     = OpenAI API key
  OPENAI_API_BASE    = Optional OpenAI base URL override
                     (default: https://api.openai.com/v1)
  CHIPVERIFY_LLM_API_KEY
                     = Generic provider API key alias used by desktop diagnostics
  CHIPVERIFY_LLM_BASE_URL
                     = Generic provider base URL alias used by desktop diagnostics
  AZURE_OPENAI_ENDPOINT
                     = Azure AI Foundry / Azure OpenAI compatible /openai/v1 endpoint
  AZURE_OPENAI_DEPLOYMENT
                     = Azure deployment name, e.g. DeepSeek-V4-Flash
  AZURE_OPENAI_API_KEY
                     = Azure API key when using key auth
  AZURE_OPENAI_USE_AAD
                     = true to use DefaultAzureCredential token auth
  NIM_API_KEY        = NVIDIA NIM API key (alias for OPENAI_API_KEY)
  NIM_API_BASE       = NVIDIA NIM base URL (default: https://integrate.api.nvidia.com/v1)
  MODEL_NAME         = Default model name
  MODEL_TEMPERATURE  = Default temperature (0.0-2.0)
  MODEL_MAX_TOKENS   = Default max tokens
"""

from __future__ import annotations

import os
import logging
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit, unquote
from urllib.parse import quote
from pathlib import Path
import re
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

load_dotenv()
load_dotenv(Path(__file__).resolve().parent / ".env")

# ── Configuration ─────────────────────────────────────────────────────

def _normalize_provider(name: Optional[str]) -> str:
    value = str(name or "gemini").strip().lower().replace("-", "_")
    aliases = {
        "azure": "azure_openai",
        "azureopenai": "azure_openai",
        "azure_ai": "azure_openai",
        "azure_ai_foundry": "azure_openai",
        "openai_sdk": "openai",
        "chatgpt": "openai",
        "convex": "convex_gateway",
        "convex_cloud": "convex_gateway",
        "convex_llm": "convex_gateway",
    }
    return aliases.get(value, value)


def _default_llm_provider() -> str:
    explicit = (
        os.getenv("CHIPVERIFY_LLM_PROVIDER")
        or os.getenv("MODEL_PROVIDER")
        or ""
    ).strip()
    if explicit:
        return _normalize_provider(explicit)
    if os.getenv("BEDROCK_API_KEY") or os.getenv("AWS_BEARER_TOKEN_BEDROCK"):
        return "bedrock"
    return "gemini"


PROVIDER = _default_llm_provider()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = (
    os.getenv("OPENAI_API_KEY")
    or os.getenv("CHIPVERIFY_OPENAI_API_KEY")
    or os.getenv("CHIPVERIFY_LLM_API_KEY")
    or os.getenv("NIM_API_KEY")
)
OPENAI_API_BASE = (
    os.getenv("OPENAI_API_BASE")
    or os.getenv("OPENAI_BASE_URL")
    or os.getenv("CHIPVERIFY_OPENAI_BASE_URL")
    or os.getenv("CHIPVERIFY_LLM_BASE_URL")
)
AZURE_OPENAI_ENDPOINT = (
    os.getenv("AZURE_OPENAI_ENDPOINT")
    or os.getenv("AZURE_OPENAI_BASE_URL")
    or os.getenv("AZURE_AI_OPENAI_ENDPOINT")
)
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT = (
    os.getenv("AZURE_OPENAI_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_MODEL")
    or "DeepSeek-V4-Flash"
)
AZURE_OPENAI_TOKEN_SCOPE = os.getenv(
    "AZURE_OPENAI_TOKEN_SCOPE", "https://ai.azure.com/.default"
)
AZURE_OPENAI_AUTH_HEADER = os.getenv("AZURE_OPENAI_AUTH_HEADER", "api-key").strip().lower()
AZURE_OPENAI_API_STYLE = os.getenv("AZURE_OPENAI_API_STYLE", "deployment").strip().lower()
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21").strip()
AZURE_OPENAI_USE_AAD = str(
    os.getenv("AZURE_OPENAI_USE_AAD", "false")
).strip().lower() in {"1", "true", "yes", "on"}
GEMINI_DEFAULT_MODEL = os.getenv("GEMINI_MODEL") or os.getenv("CHIPVERIFY_GEMINI_MODEL")
OPENAI_DEFAULT_MODEL = os.getenv("OPENAI_MODEL") or os.getenv(
    "CHIPVERIFY_LLM_MODEL_ALIAS"
)
NIM_DEFAULT_MODEL = os.getenv("NIM_MODEL")
BEDROCK_API_KEY = os.getenv("BEDROCK_API_KEY") or os.getenv("AWS_BEARER_TOKEN_BEDROCK")
BEDROCK_REGION = os.getenv("BEDROCK_REGION", "us-east-1").strip() or "us-east-1"
# Chat Completions + API-key auth use the bedrock-mantle endpoint (recommended by AWS).
# bedrock-runtime /v1/chat/completions returns UnknownOperationException for API keys.
BEDROCK_API_BASE = os.getenv("BEDROCK_API_BASE") or (
    f"https://bedrock-mantle.{BEDROCK_REGION}.api.aws/v1"
)
BEDROCK_DEFAULT_MODEL = (
    os.getenv("BEDROCK_MODEL")
    or os.getenv("CHIPVERIFY_BEDROCK_MODEL")
    or "deepseek.v3.2"
)
BEDROCK_COMPLETION_MODEL = (
    os.getenv("BEDROCK_COMPLETION_MODEL")
    or os.getenv("CHIPVERIFY_BEDROCK_COMPLETION_MODEL")
    or "openai.gpt-oss-20b"
)
CONVEX_SITE_URL = (
    os.getenv("CHIPVERIFY_CONVEX_SITE_URL")
    or os.getenv("CONVEX_SITE_URL")
    or "https://next-swordfish-62.convex.site"
).rstrip("/")
CONVEX_LLM_GATEWAY_URL = (
    os.getenv("CHIPVERIFY_CONVEX_LLM_GATEWAY_URL")
    or f"{CONVEX_SITE_URL}/api/llm/generate"
).strip()
CONVEX_GATEWAY_TIMEOUT_SEC = float(os.getenv("CHIPVERIFY_CONVEX_GATEWAY_TIMEOUT_SEC", "120"))

# Optional gpt-oss reasoning level for tab completion (low | medium | high)
COMPLETION_EXTRA_BODY: dict = {}
_completion_reasoning = os.getenv("CHIPVERIFY_COMPLETION_REASONING_EFFORT", "").strip()
if _completion_reasoning:
    COMPLETION_EXTRA_BODY["reasoning_effort"] = _completion_reasoning
_completion_extra_str = os.getenv("CHIPVERIFY_COMPLETION_EXTRA_BODY", "")
if _completion_extra_str:
    try:
        import json as _json

        COMPLETION_EXTRA_BODY.update(_json.loads(_completion_extra_str))
    except Exception:
        logger.warning("Failed to parse CHIPVERIFY_COMPLETION_EXTRA_BODY")

# NVIDIA NIM defaults
if PROVIDER == "nim":
    if not OPENAI_API_BASE:
        OPENAI_API_BASE = os.getenv(
            "NIM_API_BASE", "https://integrate.api.nvidia.com/v1"
        )
    if not OPENAI_API_KEY:
        OPENAI_API_KEY = os.getenv("NIM_API_KEY")
elif PROVIDER == "azure_openai":
    if not OPENAI_API_BASE:
        OPENAI_API_BASE = AZURE_OPENAI_ENDPOINT
    if not OPENAI_API_KEY:
        OPENAI_API_KEY = AZURE_OPENAI_API_KEY

DEFAULT_MODEL = os.getenv("MODEL_NAME", "")
DEFAULT_TEMPERATURE = float(os.getenv("MODEL_TEMPERATURE", "0.3"))
DEFAULT_MAX_TOKENS = int(os.getenv("MODEL_MAX_TOKENS", "16384"))


def get_model_max_output_tokens(
    model: Optional[str] = None,
    provider: Optional[str] = None,
    requested: Optional[int] = None,
) -> int:
    """Cap requested output tokens to the active model's provider limit."""
    from services.context_budget import get_model_max_output_tokens as _cap

    resolved = _resolve_model_name(model, provider or PROVIDER)
    return _cap(resolved, provider or PROVIDER, requested or DEFAULT_MAX_TOKENS)


def _uses_openai_max_completion_tokens(provider: Optional[str], model: Optional[str]) -> bool:
    """GPT-5/o-series chat models reject legacy max_tokens.

    Azure AI/OpenAI-compatible deployments expose the same chat-completions
    parameter contract for these models, so the check must apply to both
    OpenAI and Azure OpenAI providers.
    """
    normalized_provider = _normalize_provider(provider or PROVIDER)
    if normalized_provider not in {"openai", "azure_openai"}:
        return False
    name = str(model or "").strip().lower()
    return name.startswith(("gpt-5", "o1", "o3", "o4"))


def _add_openai_token_limit(
    payload: dict,
    *,
    provider: Optional[str],
    model: Optional[str],
    max_tokens: int,
) -> None:
    payload[
        "max_completion_tokens"
        if _uses_openai_max_completion_tokens(provider, model)
        else "max_tokens"
    ] = max_tokens


def _add_openai_temperature(
    payload: dict,
    *,
    provider: Optional[str],
    model: Optional[str],
    temperature: float,
) -> None:
    if _uses_openai_max_completion_tokens(provider, model):
        return
    payload["temperature"] = temperature


def _looks_like_gemini_model(model: Optional[str]) -> bool:
    name = str(model or "").strip().lower()
    if not name:
        return False
    return (
        name.startswith("gemini")
        or name.startswith("models/gemini")
        or "gemini" in name.split("/")[-1]
    )


# Real, currently-available Gemini model. The previous fallback
# "gemini-3.1-pro-preview" does not exist on the public API and 404s.
GEMINI_FALLBACK_MODEL = "gemini-2.5-pro"


def _normalize_gemini_model_name(model: Optional[str]) -> str:
    """Normalize Gemini aliases into the format expected by google-genai."""
    name = str(model or "").strip()
    if not name:
        return GEMINI_FALLBACK_MODEL

    # Handle full request URLs or copied endpoint strings such as
    # https://.../v1beta/models/gemini-2.5-pro:generateContent?key=...
    parsed = urlsplit(name)
    if parsed.scheme and parsed.netloc:
        name = parsed.path.rsplit("/", 1)[-1] if parsed.path else name
    else:
        name = unquote(name)

    name = name.split("?", 1)[0].split("#", 1)[0]

    lowered = name.lower()
    for suffix in (":generatecontent", ":streamgeneratecontent", ":counttokens"):
        if suffix in lowered:
            name = name[: lowered.index(suffix)]
            break

    if name.lower().startswith("models/"):
        name = name.split("/", 1)[1]

    if "/" in name and not name.lower().startswith(("tunedmodels/", "cachedcontents/")):
        tail = name.split("/")[-1]
        if "gemini" in tail.lower():
            name = tail

    name = name.strip().strip("/")
    return name or GEMINI_FALLBACK_MODEL


def _resolve_model_name(model: Optional[str], provider: str) -> str:
    """Pick a model name for the given provider.

    Important: when the provider is Gemini we MUST refuse non-Gemini model
    names (e.g. a leftover MODEL_NAME=z-ai/glm4.7 in .env), otherwise
    google-genai 404s. Always coerce to GEMINI_DEFAULT_MODEL in that case.
    """
    raw = str(model or "").strip()

    if provider == "gemini":
        # Caller-supplied name takes precedence only if it looks Gemini-shaped.
        if raw and _looks_like_gemini_model(raw):
            return _normalize_gemini_model_name(raw)
        # Otherwise prefer GEMINI_MODEL env, then any Gemini-shaped MODEL_NAME,
        # then a known-good fallback.
        if GEMINI_DEFAULT_MODEL:
            return _normalize_gemini_model_name(GEMINI_DEFAULT_MODEL)
        if DEFAULT_MODEL and _looks_like_gemini_model(DEFAULT_MODEL):
            return _normalize_gemini_model_name(DEFAULT_MODEL)
        return _normalize_gemini_model_name(GEMINI_FALLBACK_MODEL)

    if provider == "azure_openai":
        # A stale MODEL_NAME/Gemini UI alias must not be sent as an Azure
        # deployment name. Azure returns DeploymentNotFound for that exact case.
        if raw and not _looks_like_gemini_model(raw):
            return raw
        return (
            OPENAI_DEFAULT_MODEL
            or AZURE_OPENAI_DEPLOYMENT
            or (DEFAULT_MODEL if not _looks_like_gemini_model(DEFAULT_MODEL) else "")
            or "DeepSeek-V4-Flash"
        )

    if provider == "openai":
        # Do not leak a stale Gemini model from UI/demo defaults into the
        # official OpenAI API. It returns a model-not-found error and makes
        # provider switching look broken.
        if raw and not _looks_like_gemini_model(raw):
            return raw
        return (
            OPENAI_DEFAULT_MODEL
            or (DEFAULT_MODEL if not _looks_like_gemini_model(DEFAULT_MODEL) else "")
            or "gpt-5.4"
        )

    if provider == "bedrock":
        if raw and not _looks_like_gemini_model(raw):
            return raw
        return BEDROCK_DEFAULT_MODEL

    if provider == "convex_gateway":
        return (
            raw
            or os.getenv("CHIPVERIFY_CONVEX_MODEL")
            or OPENAI_DEFAULT_MODEL
            or DEFAULT_MODEL
            or "gpt-5.4"
        )

    if raw:
        return raw
    if provider == "nim":
        return (
            NIM_DEFAULT_MODEL
            or OPENAI_DEFAULT_MODEL
            or DEFAULT_MODEL
            or "meta/llama-3.1-70b-instruct"
        )
    return OPENAI_DEFAULT_MODEL or DEFAULT_MODEL or "gpt-5.4"


def _resolve_fim_model_name(model: Optional[str], provider: str) -> str:
    """Model for tab-completion FIM — separate from agentic BEDROCK_MODEL."""
    if model:
        return _resolve_model_name(model, provider)
    explicit = os.getenv("CHIPVERIFY_COMPLETION_MODEL", "").strip()
    if explicit and (provider != "bedrock" or not _looks_like_gemini_model(explicit)):
        return _resolve_model_name(explicit, provider)
    if provider == "bedrock":
        return BEDROCK_COMPLETION_MODEL
    return _resolve_model_name(None, provider)


def _resolve_openai_base_url(provider: str, base_url: Optional[str] = None) -> str:
    if base_url:
        return base_url
    if provider == "azure_openai":
        return AZURE_OPENAI_ENDPOINT or OPENAI_API_BASE or ""
    if provider == "bedrock":
        return BEDROCK_API_BASE
    if provider == "nim":
        return OPENAI_API_BASE or "https://integrate.api.nvidia.com/v1"
    if provider == "openai":
        return OPENAI_API_BASE or "https://api.openai.com/v1"
    return OPENAI_API_BASE or "http://localhost:8080/v1"


def _resolve_openai_api_key(provider: str):
    if provider == "azure_openai" and AZURE_OPENAI_USE_AAD:
        return _get_azure_token_provider()
    if provider == "azure_openai":
        return AZURE_OPENAI_API_KEY or OPENAI_API_KEY or "local"
    if provider == "openai":
        return OPENAI_API_KEY or ""
    if provider == "bedrock":
        return BEDROCK_API_KEY or "local"
    return OPENAI_API_KEY or "local"


def _get_azure_token_provider():
    cache_key = f"azure-aad:{AZURE_OPENAI_TOKEN_SCOPE}"
    if cache_key not in _providers:
        try:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        except ImportError as exc:
            raise RuntimeError(
                "azure-identity is required when AZURE_OPENAI_USE_AAD=true"
            ) from exc
        _providers[cache_key] = get_bearer_token_provider(
            DefaultAzureCredential(),
            AZURE_OPENAI_TOKEN_SCOPE,
        )
    return _providers[cache_key]


def _use_azure_deployment_api(provider: str) -> bool:
    return _normalize_provider(provider) == "azure_openai" and AZURE_OPENAI_API_STYLE in {
        "deployment",
        "azure_deployment",
        "legacy",
    }


def _azure_resource_root(base_url: Optional[str] = None) -> str:
    base = (base_url or AZURE_OPENAI_ENDPOINT or OPENAI_API_BASE or "").rstrip("/")
    for suffix in ("/openai/v1", "/openai"):
        if base.lower().endswith(suffix):
            return base[: -len(suffix)].rstrip("/")
    return base


def _azure_deployment_chat_url(model: str, base_url: Optional[str] = None) -> str:
    deployment = quote(model, safe="")
    return (
        f"{_azure_resource_root(base_url)}/openai/deployments/"
        f"{deployment}/chat/completions?api-version={AZURE_OPENAI_API_VERSION}"
    )


def _azure_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if AZURE_OPENAI_USE_AAD:
        headers["Authorization"] = f"Bearer {_get_azure_token_provider()()}"
        return headers

    key = AZURE_OPENAI_API_KEY or OPENAI_API_KEY or ""
    if not key:
        return headers

    if AZURE_OPENAI_AUTH_HEADER in {"api-key", "apikey", "api_key"}:
        headers["api-key"] = key
    elif AZURE_OPENAI_AUTH_HEADER == "both":
        headers["api-key"] = key
        headers["Authorization"] = f"Bearer {key}"
    else:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _azure_deployment_completion(
    *,
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    base_url: Optional[str] = None,
    tools: Optional[list[dict]] = None,
    tool_choice: str = "auto",
    extra_body: Optional[dict] = None,
) -> dict:
    import requests

    payload: dict = {
        "messages": messages,
    }
    _add_openai_temperature(
        payload,
        provider="azure_openai",
        model=model,
        temperature=temperature,
    )
    _add_openai_token_limit(
        payload,
        provider="azure_openai",
        model=model,
        max_tokens=max_tokens,
    )
    if tools:
        payload["tools"] = tools
    if tools and tool_choice != "auto":
        payload["tool_choice"] = tool_choice
    if extra_body:
        payload.update(extra_body)

    response = requests.post(
        _azure_deployment_chat_url(model, base_url),
        headers=_azure_headers(),
        json=payload,
        timeout=max(30, int(os.getenv("CHIPVERIFY_LLM_TIMEOUT_SECONDS", "300"))),
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Azure deployment request failed ({response.status_code}): {response.text[:800]}"
        )
    return response.json()


# Extra body for reasoning models (e.g. GLM-4.7 thinking mode)
EXTRA_BODY = {}
_extra_body_str = os.getenv("MODEL_EXTRA_BODY", "")
if _extra_body_str:
    try:
        import json

        EXTRA_BODY = json.loads(_extra_body_str)
    except Exception:
        logger.warning(f"Failed to parse MODEL_EXTRA_BODY: {_extra_body_str}")


# ── Provider Registry ─────────────────────────────────────────────────

_providers: dict = {}


def _get_gemini_client():
    key = f"gemini:{GOOGLE_API_KEY or ''}"
    if key not in _providers:
        _providers[key] = genai.Client(api_key=GOOGLE_API_KEY)
    return _providers[key]


def _get_gemini_fim_client(timeout_sec: float = 3.0):
    """Fast Gemini client for tab completion — no retries, strict timeout."""
    timeout_ms = max(500, int(timeout_sec * 1000))
    key = f"gemini_fim:{GOOGLE_API_KEY or ''}:{timeout_ms}"
    if key not in _providers:
        _providers[key] = genai.Client(
            api_key=GOOGLE_API_KEY,
            http_options=types.HttpOptions(
                timeout=timeout_ms,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
    return _providers[key]


def _get_openai_client(base_url: Optional[str] = None, provider: Optional[str] = None):
    provider = _normalize_provider(provider or PROVIDER)
    resolved_base_url = _resolve_openai_base_url(provider, base_url)
    auth_mode = "aad" if provider == "azure_openai" and AZURE_OPENAI_USE_AAD else "key"
    key = f"openai:{provider}:{resolved_base_url}:{auth_mode}"
    if key not in _providers:
        from openai import OpenAI

        _providers[key] = OpenAI(
            api_key=_resolve_openai_api_key(provider),
            base_url=resolved_base_url or "http://localhost:8080/v1",
        )
    return _providers[key]


# ── Unified Generation Function ───────────────────────────────────────


def generate(
    system_prompt: str,
    user_message: str,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    provider: Optional[str] = None,
    extra_body: Optional[dict] = None,
) -> tuple[str, str]:
    """
    Generate text using the configured LLM provider.

    Args:
        system_prompt: System instruction / role
        user_message: User prompt
        model: Model name (overrides default)
        temperature: Sampling temperature (overrides default)
        max_tokens: Max output tokens (overrides default)
        provider: Force specific provider ("gemini" | "openai" | "nim")
        extra_body: Additional parameters for the API call

    Returns:
        Tuple of (content, reasoning) where reasoning may be empty for non-thinking models
    """
    provider = provider or PROVIDER
    model = _resolve_model_name(model, provider)
    temperature = temperature if temperature is not None else DEFAULT_TEMPERATURE
    max_tokens = max_tokens or DEFAULT_MAX_TOKENS
    extra_body = extra_body or EXTRA_BODY

    if provider == "convex_gateway":
        return _generate_convex_gateway(
            system_prompt,
            user_message,
            model,
            temperature,
            max_tokens,
            extra_body=extra_body,
        )

    if provider == "gemini":
        content = _generate_gemini(
            system_prompt, user_message, model, temperature, max_tokens
        )
        return content, ""
    else:
        # openai, nim, or any OpenAI-compatible endpoint
        base_url = _resolve_openai_base_url(provider)
        return _generate_openai(
            system_prompt,
            user_message,
            model,
            temperature,
            max_tokens,
            base_url=base_url,
            provider=provider,
            extra_body=extra_body,
        )


def generate_stream(
    system_prompt: str,
    user_message: str,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    provider: Optional[str] = None,
    extra_body: Optional[dict] = None,
):
    """
    Generate text with streaming output.

    Yields:
        str: Chunks of generated text
    """
    provider = provider or PROVIDER
    model = _resolve_model_name(model, provider)
    temperature = temperature if temperature is not None else DEFAULT_TEMPERATURE
    max_tokens = max_tokens or DEFAULT_MAX_TOKENS
    extra_body = extra_body or EXTRA_BODY

    if provider == "convex_gateway":
        content, reasoning = _generate_convex_gateway(
            system_prompt,
            user_message,
            model,
            temperature,
            max_tokens,
            extra_body=extra_body,
        )
        if reasoning:
            yield reasoning
        if content:
            yield content
        return

    if provider == "gemini":
        yield from _generate_gemini_stream(
            system_prompt, user_message, model, temperature, max_tokens
        )
    else:
        base_url = _resolve_openai_base_url(provider)
        yield from _generate_openai_stream(
            system_prompt,
            user_message,
            model,
            temperature,
            max_tokens,
            base_url=base_url,
            provider=provider,
            extra_body=extra_body,
        )


# ── Gemini Implementation ────────────────────────────────────────────


def _generate_gemini(
    system_prompt: str,
    user_message: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> str:
    client = _get_gemini_client()
    response = client.models.generate_content(
        model=_normalize_gemini_model_name(model),
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
        ),
    )
    return response.text or ""


def _generate_gemini_stream(
    system_prompt: str,
    user_message: str,
    model: str,
    temperature: float,
    max_tokens: int,
):
    client = _get_gemini_client()
    config = types.GenerateContentConfig(
        system_instruction=system_prompt or None,
        temperature=temperature,
        max_output_tokens=max_tokens,
    )
    stream_method = getattr(client.models, "generate_content_stream", None)
    if stream_method is not None:
        response = stream_method(
            model=_normalize_gemini_model_name(model),
            contents=user_message,
            config=config,
        )
    else:
        response = client.models.generate_content(
            model=_normalize_gemini_model_name(model),
            contents=user_message,
            config=config,
            stream=True,
        )
    for chunk in response:
        if getattr(chunk, "text", None):
            yield chunk.text


# ── OpenAI / NIM Implementation ──────────────────────────────────────


def _generate_openai(
    system_prompt: str,
    user_message: str,
    model: str,
    temperature: float,
    max_tokens: int,
    base_url: Optional[str] = None,
    provider: Optional[str] = None,
    extra_body: Optional[dict] = None,
) -> tuple[str, str]:
    """
    Generate text using OpenAI-compatible API.
    Returns tuple of (content, reasoning) where reasoning may be empty for non-thinking models.
    """
    if _use_azure_deployment_api(provider or PROVIDER):
        result = _azure_deployment_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            base_url=base_url,
            extra_body=extra_body,
        )
        message = result["choices"][0]["message"]
        return message.get("content") or "", message.get("reasoning_content") or ""

    client = _get_openai_client(base_url, provider)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    kwargs = {
        "model": model,
        "messages": messages,
    }
    _add_openai_temperature(
        kwargs,
        provider=provider,
        model=model,
        temperature=temperature,
    )
    _add_openai_token_limit(
        kwargs,
        provider=provider,
        model=model,
        max_tokens=max_tokens,
    )
    if extra_body:
        kwargs["extra_body"] = extra_body

    completion = client.chat.completions.create(**kwargs)
    message = completion.choices[0].message
    content = message.content or ""
    reasoning = getattr(message, "reasoning_content", None) or ""
    return content, reasoning


def _generate_openai_stream(
    system_prompt: str,
    user_message: str,
    model: str,
    temperature: float,
    max_tokens: int,
    base_url: Optional[str] = None,
    provider: Optional[str] = None,
    extra_body: Optional[dict] = None,
):
    if _use_azure_deployment_api(provider or PROVIDER):
        content, reasoning = _generate_openai(
            system_prompt,
            user_message,
            model,
            temperature,
            max_tokens,
            base_url=base_url,
            provider=provider,
            extra_body=extra_body,
        )
        if reasoning:
            yield reasoning
        if content:
            yield content
        return

    client = _get_openai_client(base_url, provider)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    kwargs = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    _add_openai_temperature(
        kwargs,
        provider=provider,
        model=model,
        temperature=temperature,
    )
    _add_openai_token_limit(
        kwargs,
        provider=provider,
        model=model,
        max_tokens=max_tokens,
    )
    if extra_body:
        kwargs["extra_body"] = extra_body

    completion = client.chat.completions.create(**kwargs)
    for chunk in completion:
        if not getattr(chunk, "choices", None):
            continue
        if len(chunk.choices) == 0:
            continue
        delta = chunk.choices[0].delta
        # Yield reasoning content first (for thinking models)
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            yield reasoning
        content = getattr(delta, "content", None)
        if content:
            yield content


# ── LangChain-Compatible LLM Wrapper ─────────────────────────────────


def get_langchain_llm(
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    provider: Optional[str] = None,
):
    """
    Return a LangChain-compatible chat model instance.
    Used by RTL_designer/agents/nodes.py for LangGraph pipeline.
    """
    provider = provider or PROVIDER
    model = _resolve_model_name(model, provider)
    temperature = temperature if temperature is not None else DEFAULT_TEMPERATURE
    max_tokens = max_tokens or DEFAULT_MAX_TOKENS

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            google_api_key=GOOGLE_API_KEY,
        )
    else:
        from langchain_openai import ChatOpenAI

        kwargs = {
            "model": model,
            "api_key": _resolve_openai_api_key(provider),
            "base_url": _resolve_openai_base_url(provider) or "http://localhost:8080/v1",
        }
        _add_openai_temperature(
            kwargs,
            provider=provider,
            model=model,
            temperature=temperature,
        )
        if _uses_openai_max_completion_tokens(provider, model):
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
        return ChatOpenAI(**kwargs)


# ── Native Function Calling ───────────────────────────────────────────

from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, List


@dataclass
class ToolCall:
    """Represents a single tool/function call from the LLM."""

    id: str
    name: str
    arguments: dict


@dataclass
class TokenUsage:
    """Normalized token accounting across supported providers."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    provider: str = ""
    model: str = ""
    is_estimated: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_ai_sdk_usage(self) -> dict:
        return {
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens or self.input_tokens + self.output_tokens,
        }

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens or self.input_tokens + self.output_tokens,
            "provider": self.provider,
            "model": self.model,
            "is_estimated": self.is_estimated,
            "details": self.details,
        }


@dataclass
class ChatResponse:
    """Complete response from a chat-with-tools call."""

    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    reasoning: str = ""
    usage: Optional[TokenUsage] = None


def _safe_token_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _rough_token_count(text: Any) -> int:
    value = text if isinstance(text, str) else str(text or "")
    if not value:
        return 0
    return max(1, (len(value) + 3) // 4)


def _estimate_chat_usage(
    messages: list[dict],
    output_text: str,
    *,
    provider: str,
    model: str,
    details: Optional[dict] = None,
) -> TokenUsage:
    input_text = "\n".join(str(message.get("content") or "") for message in messages)
    input_tokens = _rough_token_count(input_text)
    output_tokens = _rough_token_count(output_text)
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        provider=provider,
        model=model,
        is_estimated=True,
        details=details or {},
    )


def _convex_machine_id() -> str:
    return (
        os.getenv("CHIPVERIFY_DESKTOP_MACHINE_ID")
        or os.getenv("CHIPVERIFY_MACHINE_ID")
        or "local-dev"
    )


def _convex_activation_hash() -> str:
    return os.getenv("CHIPVERIFY_DESKTOP_ACTIVATION_HASH", "")


def _convex_gateway_post(payload: dict) -> dict:
    if not CONVEX_LLM_GATEWAY_URL:
        raise RuntimeError("CHIPVERIFY_CONVEX_LLM_GATEWAY_URL or CHIPVERIFY_CONVEX_SITE_URL is not configured")

    request_payload = {
        "machineId": _convex_machine_id(),
        "activationHash": _convex_activation_hash(),
        **payload,
    }
    body = json.dumps(request_payload, default=str).encode("utf-8")
    request = urllib.request.Request(
        CONVEX_LLM_GATEWAY_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=CONVEX_GATEWAY_TIMEOUT_SEC) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"error": {"message": raw}}
        error = parsed.get("error") if isinstance(parsed, dict) else None
        message = (
            error.get("message")
            if isinstance(error, dict)
            else f"Convex gateway request failed ({exc.code})"
        )
        raise RuntimeError(message) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Convex gateway is unreachable: {exc.reason}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Convex gateway returned invalid JSON") from exc

    if not parsed.get("ok", False):
        error = parsed.get("error") if isinstance(parsed, dict) else None
        message = (
            error.get("message")
            if isinstance(error, dict)
            else "Convex gateway rejected the request"
        )
        raise RuntimeError(message)
    return parsed


def _usage_from_convex(payload: dict, *, provider: str, model: str) -> Optional[TokenUsage]:
    usage = payload.get("usage") or {}
    input_tokens = _safe_token_int(
        usage.get("inputTokens")
        or usage.get("input_tokens")
        or usage.get("prompt_tokens")
    )
    output_tokens = _safe_token_int(
        usage.get("outputTokens")
        or usage.get("output_tokens")
        or usage.get("completion_tokens")
    )
    total_tokens = _safe_token_int(
        usage.get("totalTokens")
        or usage.get("total_tokens")
        or input_tokens + output_tokens
    )
    if total_tokens <= 0:
        return None
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        provider=str(usage.get("provider") or provider or "convex_gateway"),
        model=str(usage.get("model") or model or ""),
        is_estimated=bool(usage.get("isEstimated") or usage.get("is_estimated") or False),
        details={"source": "convex_gateway", **(usage.get("details") or {})},
    )


def _generate_convex_gateway(
    system_prompt: str,
    user_message: str,
    model: str,
    temperature: float,
    max_tokens: int,
    *,
    extra_body: Optional[dict] = None,
) -> tuple[str, str]:
    payload = _convex_gateway_post(
        {
            "mode": "generate",
            "source": (extra_body or {}).get("chipverify_source", "backend_generate"),
            "systemPrompt": system_prompt,
            "userMessage": user_message,
            "model": model,
            "temperature": temperature,
            "maxTokens": max_tokens,
            "extraBody": {
                key: value
                for key, value in (extra_body or {}).items()
                if not str(key).startswith("chipverify_")
            },
        }
    )
    return str(payload.get("content") or ""), str(payload.get("reasoning") or "")


def _chat_with_tools_convex_gateway(
    messages: list[dict],
    tools: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    tool_choice: str,
    extra_body: Optional[dict],
) -> ChatResponse:
    payload = _convex_gateway_post(
        {
            "mode": "chat",
            "source": (extra_body or {}).get("chipverify_source", "backend_chat"),
            "messages": messages,
            "tools": tools,
            "toolChoice": tool_choice,
            "model": model,
            "temperature": temperature,
            "maxTokens": max_tokens,
            "extraBody": {
                key: value
                for key, value in (extra_body or {}).items()
                if not str(key).startswith("chipverify_")
            },
        }
    )
    tool_calls = [
        ToolCall(
            id=str(item.get("id") or ""),
            name=str(item.get("name") or ""),
            arguments=item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
        )
        for item in payload.get("toolCalls") or []
        if isinstance(item, dict)
    ]
    provider = str(payload.get("provider") or "convex_gateway")
    resolved_model = str(payload.get("model") or model)
    return ChatResponse(
        content=str(payload.get("content") or ""),
        tool_calls=tool_calls,
        finish_reason=str(payload.get("finishReason") or "stop"),
        reasoning=str(payload.get("reasoning") or ""),
        usage=_usage_from_convex(payload, provider=provider, model=resolved_model)
        or _estimate_chat_usage(
            messages,
            str(payload.get("content") or ""),
            provider=provider,
            model=resolved_model,
            details={"source": "estimate_after_missing_convex_usage"},
        ),
    )


def _usage_from_openai(
    usage: Any,
    *,
    provider: str,
    model: str,
    details: Optional[dict] = None,
) -> Optional[TokenUsage]:
    if not usage:
        return None
    input_tokens = _safe_token_int(
        getattr(usage, "prompt_tokens", None)
        if not isinstance(usage, dict)
        else usage.get("prompt_tokens")
    )
    output_tokens = _safe_token_int(
        getattr(usage, "completion_tokens", None)
        if not isinstance(usage, dict)
        else usage.get("completion_tokens")
    )
    total_tokens = _safe_token_int(
        getattr(usage, "total_tokens", None)
        if not isinstance(usage, dict)
        else usage.get("total_tokens")
    )
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens
    if total_tokens <= 0:
        return None

    usage_details = dict(details or {})
    for attr in ("prompt_tokens_details", "completion_tokens_details"):
        value = getattr(usage, attr, None) if not isinstance(usage, dict) else usage.get(attr)
        if value:
            if hasattr(value, "model_dump"):
                value = value.model_dump()
            usage_details[attr] = value

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        provider=provider,
        model=model,
        details=usage_details,
    )


def _usage_from_gemini(
    usage_metadata: Any,
    *,
    provider: str,
    model: str,
) -> Optional[TokenUsage]:
    if not usage_metadata:
        return None

    def pick(*names: str) -> int:
        for name in names:
            if isinstance(usage_metadata, dict):
                value = usage_metadata.get(name)
            else:
                value = getattr(usage_metadata, name, None)
            if value is not None:
                return _safe_token_int(value)
        return 0

    input_tokens = pick("prompt_token_count", "promptTokenCount")
    output_tokens = pick("candidates_token_count", "candidatesTokenCount", "response_token_count", "responseTokenCount")
    total_tokens = pick("total_token_count", "totalTokenCount")
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens
    if total_tokens <= 0:
        return None

    details = {}
    for name in (
        "cached_content_token_count",
        "cachedContentTokenCount",
        "tool_use_prompt_token_count",
        "toolUsePromptTokenCount",
        "thoughts_token_count",
        "thoughtsTokenCount",
    ):
        value = usage_metadata.get(name) if isinstance(usage_metadata, dict) else getattr(usage_metadata, name, None)
        if value is not None:
            details[name] = _safe_token_int(value)

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        provider=provider,
        model=model,
        details=details,
    )


def chat_with_tools(
    messages: list[dict],
    tools: list[dict],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    provider: Optional[str] = None,
    tool_choice: str = "auto",
    extra_body: Optional[dict] = None,
) -> ChatResponse:
    """
    Chat with native function calling support.

    Args:
        messages: List of {role, content} messages (role can be system/user/assistant/tool)
        tools: List of tool definitions in OpenAI function schema format
        model: Model name override
        temperature: Temperature override
        max_tokens: Max tokens override
        provider: Provider override
        tool_choice: "auto", "none", or {"type": "function", "function": {"name": "..."}}
        extra_body: Additional API parameters

    Returns:
        ChatResponse with content, tool_calls, finish_reason, and reasoning
    """
    provider = provider or PROVIDER
    model = _resolve_model_name(model, provider)
    temperature = temperature if temperature is not None else DEFAULT_TEMPERATURE
    max_tokens = max_tokens or DEFAULT_MAX_TOKENS
    extra_body = extra_body or EXTRA_BODY

    if provider == "convex_gateway":
        return _chat_with_tools_convex_gateway(
            messages,
            tools,
            model,
            temperature,
            max_tokens,
            tool_choice,
            extra_body,
        )

    if provider == "gemini":
        return _chat_with_tools_gemini(
            messages, tools, model, temperature, max_tokens, tool_choice
        )
    else:
        base_url = _resolve_openai_base_url(provider)
        return _chat_with_tools_openai(
            messages,
            tools,
            model,
            temperature,
            max_tokens,
            base_url,
            provider,
            tool_choice,
            extra_body,
        )


async def chat_with_tools_stream(
    messages: list[dict],
    tools: list[dict],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    provider: Optional[str] = None,
    tool_choice: str = "auto",
    extra_body: Optional[dict] = None,
) -> AsyncGenerator[ChatResponse, None]:
    """
    Streaming chat with native function calling support.

    Yields incremental ChatResponse chunks. The final chunk will have
    finish_reason set to "stop", "tool_calls", or "length".
    """
    provider = provider or PROVIDER
    model = _resolve_model_name(model, provider)
    temperature = temperature if temperature is not None else DEFAULT_TEMPERATURE
    max_tokens = max_tokens or DEFAULT_MAX_TOKENS
    extra_body = extra_body or EXTRA_BODY

    if provider == "convex_gateway":
        yield _chat_with_tools_convex_gateway(
            messages,
            tools,
            model,
            temperature,
            max_tokens,
            tool_choice,
            extra_body,
        )
        return

    if provider == "gemini":
        async for chunk in _chat_with_tools_gemini_stream(
            messages, tools, model, temperature, max_tokens, tool_choice
        ):
            yield chunk
    else:
        base_url = _resolve_openai_base_url(provider)
        async for chunk in _chat_with_tools_openai_stream(
            messages,
            tools,
            model,
            temperature,
            max_tokens,
            base_url,
            provider,
            tool_choice,
            extra_body,
        ):
            yield chunk


def _openai_chat_messages_to_gemini_contents(messages: list[dict]) -> tuple[str, list]:
    """Map OpenAI-format chat messages to Gemini ``Content`` objects.

    Multi-turn tools require (a) model turns with ``function_call`` parts matching
    assistant ``tool_calls``, and (b) tool turns with ``FunctionResponse`` whose
    ``name`` is the tool id (e.g. createFile), ``id`` matches the call id — not
    using ``tool_call_id`` as the function name (that broke tool feedback loops).
    """
    import json

    system_content = ""
    contents = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""
        if role == "system":
            system_content = content if isinstance(content, str) else str(content)
            continue
        if role == "user":
            text = content if isinstance(content, str) else str(content)
            contents.append(
                types.Content(role="user", parts=[types.Part.from_text(text=text)])
            )
            continue
        if role == "assistant":
            parts = []
            if isinstance(content, str) and content.strip():
                parts.append(types.Part.from_text(text=content))
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                fname = fn.get("name") or ""
                tc_id = tc.get("id")
                args_raw = fn.get("arguments") or "{}"
                try:
                    args = (
                        json.loads(args_raw)
                        if isinstance(args_raw, str)
                        else dict(args_raw or {})
                    )
                except (json.JSONDecodeError, TypeError):
                    args = {}
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            id=tc_id,
                            name=fname,
                            args=args,
                        )
                    )
                )
            if not parts:
                parts.append(types.Part.from_text(text=""))
            contents.append(types.Content(role="model", parts=parts))
            continue
        if role == "tool":
            tool_call_id = msg.get("tool_call_id", "") or ""
            fn_name = (msg.get("name") or "").strip()
            try:
                result_data = (
                    json.loads(content) if isinstance(content, str) else content
                )
            except (json.JSONDecodeError, TypeError):
                result_data = {"output": content}
            if not isinstance(result_data, dict):
                result_data = {"output": result_data}
            if not fn_name:
                # Legacy messages — infer common tools so Gemini still correlates.
                if isinstance(result_data, dict) and "artifact" in result_data:
                    fn_name = "createFile"
                elif isinstance(result_data, dict) and "diff" in result_data:
                    fn_name = "applyCodeToFile"
                elif isinstance(result_data, dict) and "files" in result_data:
                    fn_name = "listFiles"
                else:
                    fn_name = "tool_result"
                    logger.warning(
                        "Gemini tool message missing ``name`` (tool_call_id=%s); "
                        "using placeholder — upgrade agent loop to set ``name``",
                        tool_call_id[:20] if tool_call_id else "",
                    )
            contents.append(
                types.Content(
                    role="tool",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=tool_call_id or None,
                                name=fn_name,
                                response=result_data,
                            )
                        )
                    ],
                )
            )
            continue

    return system_content, contents


def _chat_with_tools_gemini(
    messages: list[dict],
    tools: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    tool_choice: str,
) -> ChatResponse:
    system_content, contents = _openai_chat_messages_to_gemini_contents(messages)

    gemini_tools = []
    for t in tools:
        function_declaration = types.FunctionDeclaration(
            name=t["function"]["name"],
            description=t["function"].get("description", ""),
            parameters_json_schema=t["function"].get(
                "parameters", {"type": "object", "properties": {}}
            ),
        )
        gemini_tools.append(types.Tool(function_declarations=[function_declaration]))

    # Map OpenAI-style tool_choice to Gemini ToolConfig
    tool_config = None
    if tool_choice == "none":
        tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="NONE")
        )
    elif tool_choice == "auto":
        tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="AUTO")
        )
    elif tool_choice == "required":
        tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="ANY")
        )
    elif isinstance(tool_choice, dict):
        # e.g. {"type": "function", "function": {"name": "createFile"}}
        fn_name = tool_choice.get("function", {}).get("name")
        tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode="ANY",
                allowed_function_names=[fn_name] if fn_name else [],
            )
        )

    config = types.GenerateContentConfig(
        system_instruction=system_content or None,
        temperature=temperature,
        max_output_tokens=max_tokens,
        tools=gemini_tools or None,
        tool_config=tool_config,
    )

    client = _get_gemini_client()
    response = client.models.generate_content(
        model=_normalize_gemini_model_name(model),
        contents=contents,
        config=config,
    )

    text_parts = []
    tool_calls = []

    if getattr(response, "candidates", None):
        candidate = response.candidates[0]
        if getattr(candidate, "content", None) and getattr(
            candidate.content, "parts", None
        ):
            for part in candidate.content.parts:
                if getattr(part, "text", None):
                    text_parts.append(part.text)
                function_call = getattr(part, "function_call", None)
                if function_call:
                    args = dict(function_call.args or {})
                    tool_calls.append(
                        ToolCall(
                            id=function_call.name,
                            name=function_call.name,
                            arguments=args,
                        )
                    )

    if not tool_calls and getattr(response, "function_calls", None):
        for function_call in response.function_calls:
            args = dict(getattr(function_call, "args", {}) or {})
            tool_calls.append(
                ToolCall(
                    id=function_call.name,
                    name=function_call.name,
                    arguments=args,
                )
            )

    finish_reason = "stop"
    if getattr(response, "candidates", None):
        finish = getattr(response.candidates[0], "finish_reason", None)
        if finish:
            fr = str(finish).lower()
            if "tool" in fr or "function" in fr:
                finish_reason = "tool_calls"
            elif "length" in fr or "max" in fr:
                finish_reason = "length"

    return ChatResponse(
        content="\n".join(text_parts),
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=_usage_from_gemini(
            getattr(response, "usage_metadata", None),
            provider="gemini",
            model=model,
        )
        or _estimate_chat_usage(
            messages,
            "\n".join(text_parts),
            provider="gemini",
            model=model,
            details={"source": "estimate_after_missing_gemini_usage"},
        ),
    )


def _chat_with_tools_openai(
    messages: list[dict],
    tools: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    base_url: Optional[str],
    provider: Optional[str],
    tool_choice: str,
    extra_body: Optional[dict],
) -> ChatResponse:
    import json

    oai_tools = []
    for t in tools:
        oai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "parameters": t["function"].get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                },
            }
        )

    if _use_azure_deployment_api(provider or PROVIDER):
        result = _azure_deployment_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            base_url=base_url,
            tools=oai_tools,
            tool_choice=tool_choice,
            extra_body=extra_body,
        )
        message = result["choices"][0]["message"]
        choice = result["choices"][0]
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""
        tool_calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append(
                ToolCall(
                    id=tc.get("id") or "",
                    name=fn.get("name") or "",
                    arguments=args,
                )
            )
        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason") or "stop",
            reasoning=reasoning,
            usage=_usage_from_openai(
                result.get("usage"),
                provider=provider or PROVIDER,
                model=model,
            )
            or _estimate_chat_usage(
                messages,
                content,
                provider=provider or PROVIDER,
                model=model,
                details={"source": "estimate_after_missing_openai_usage"},
            ),
        )

    client = _get_openai_client(base_url, provider)

    kwargs = {
        "model": model,
        "messages": messages,
        "tools": oai_tools,
    }
    _add_openai_temperature(
        kwargs,
        provider=provider,
        model=model,
        temperature=temperature,
    )
    _add_openai_token_limit(
        kwargs,
        provider=provider,
        model=model,
        max_tokens=max_tokens,
    )
    if tool_choice != "auto":
        kwargs["tool_choice"] = tool_choice
    if extra_body:
        kwargs["extra_body"] = extra_body

    completion = client.chat.completions.create(**kwargs)
    message = completion.choices[0].message
    choice = completion.choices[0]

    content = message.content or ""
    reasoning = getattr(message, "reasoning_content", None) or ""

    tool_calls = []
    if message.tool_calls:
        for tc in message.tool_calls:
            try:
                args = (
                    json.loads(tc.function.arguments) if tc.function.arguments else {}
                )
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                )
            )

    finish_reason = choice.finish_reason or "stop"
    if finish_reason == "tool_calls":
        pass
    elif finish_reason == "stop":
        pass
    elif finish_reason == "length":
        pass
    else:
        finish_reason = "stop"

    return ChatResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        reasoning=reasoning,
        usage=_usage_from_openai(
            getattr(completion, "usage", None),
            provider=provider or PROVIDER,
            model=model,
        )
        or _estimate_chat_usage(
            messages,
            content,
            provider=provider or PROVIDER,
            model=model,
            details={"source": "estimate_after_missing_openai_usage"},
        ),
    )


async def _chat_with_tools_gemini_stream(
    messages: list[dict],
    tools: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    tool_choice: str,
) -> AsyncGenerator[ChatResponse, None]:
    system_content, contents = _openai_chat_messages_to_gemini_contents(messages)

    gemini_tools = []
    for t in tools:
        function_declaration = types.FunctionDeclaration(
            name=t["function"]["name"],
            description=t["function"].get("description", ""),
            parameters_json_schema=t["function"].get(
                "parameters", {"type": "object", "properties": {}}
            ),
        )
        gemini_tools.append(types.Tool(function_declarations=[function_declaration]))

    config = types.GenerateContentConfig(
        system_instruction=system_content or None,
        temperature=temperature,
        max_output_tokens=max_tokens,
        tools=gemini_tools or None,
    )

    client = _get_gemini_client()
    response = client.models.generate_content_stream(
        model=_normalize_gemini_model_name(model),
        contents=contents,
        config=config,
    )

    accumulated_text = ""
    accumulated_tool_calls = []

    for chunk in response:
        delta_text = ""
        delta_tool_calls = []
        usage = _usage_from_gemini(
            getattr(chunk, "usage_metadata", None),
            provider="gemini",
            model=model,
        )

        if getattr(chunk, "candidates", None):
            candidate = chunk.candidates[0]
            if getattr(candidate, "content", None) and getattr(
                candidate.content, "parts", None
            ):
                for part in candidate.content.parts:
                    if getattr(part, "text", None):
                        delta_text = part.text
                        accumulated_text += delta_text
                    function_call = getattr(part, "function_call", None)
                    if function_call:
                        args = dict(function_call.args or {})
                        tc = ToolCall(
                            id=function_call.name,
                            name=function_call.name,
                            arguments=args,
                        )
                        delta_tool_calls.append(tc)
                        accumulated_tool_calls.append(tc)

        finish_reason = "stop"
        if getattr(chunk, "candidates", None):
            finish = getattr(chunk.candidates[0], "finish_reason", None)
            if finish:
                fr = str(finish).lower()
                if "tool" in fr or "function" in fr:
                    finish_reason = "tool_calls"
                elif "length" in fr or "max" in fr:
                    finish_reason = "length"

        yield ChatResponse(
            content=delta_text,
            tool_calls=delta_tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )


async def _chat_with_tools_openai_stream(
    messages: list[dict],
    tools: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    base_url: Optional[str],
    provider: Optional[str],
    tool_choice: str,
    extra_body: Optional[dict],
) -> AsyncGenerator[ChatResponse, None]:
    import json

    oai_tools = []
    for t in tools:
        oai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "parameters": t["function"].get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                },
            }
        )

    if _use_azure_deployment_api(provider or PROVIDER):
        yield _chat_with_tools_openai(
            messages,
            tools,
            model,
            temperature,
            max_tokens,
            base_url,
            provider,
            tool_choice,
            extra_body,
        )
        return

    client = _get_openai_client(base_url, provider)

    kwargs = {
        "model": model,
        "messages": messages,
        "tools": oai_tools,
        "stream": True,
    }
    _add_openai_temperature(
        kwargs,
        provider=provider,
        model=model,
        temperature=temperature,
    )
    _add_openai_token_limit(
        kwargs,
        provider=provider,
        model=model,
        max_tokens=max_tokens,
    )
    if (provider or PROVIDER) == "openai":
        kwargs["stream_options"] = {"include_usage": True}
    if tool_choice != "auto":
        kwargs["tool_choice"] = tool_choice
    if extra_body:
        kwargs["extra_body"] = extra_body

    completion = client.chat.completions.create(**kwargs)
    streamed_text_parts: list[str] = []
    emitted_usage = False

    for chunk in completion:
        usage = _usage_from_openai(
            getattr(chunk, "usage", None),
            provider=provider or PROVIDER,
            model=model,
        )
        if usage and (not getattr(chunk, "choices", None) or len(chunk.choices) == 0):
            emitted_usage = True
            yield ChatResponse(content="", finish_reason="stop", usage=usage)
            continue

        if not getattr(chunk, "choices", None) or len(chunk.choices) == 0:
            continue

        delta = chunk.choices[0].delta
        finish_reason = chunk.choices[0].finish_reason or ""

        delta_text = getattr(delta, "content", None) or ""
        if delta_text:
            streamed_text_parts.append(delta_text)

        delta_tool_calls = []
        if delta.tool_calls:
            for tc in delta.tool_calls:
                try:
                    args = (
                        json.loads(tc.function.arguments)
                        if tc.function.arguments
                        else {}
                    )
                except (json.JSONDecodeError, TypeError):
                    args = {}
                delta_tool_calls.append(
                    ToolCall(
                        id=tc.id or "",
                        name=tc.function.name or "",
                        arguments=args,
                    )
                )

        mapped_finish = "stop"
        if finish_reason == "tool_calls":
            mapped_finish = "tool_calls"
        elif finish_reason == "length":
            mapped_finish = "length"
        elif finish_reason == "stop":
            mapped_finish = "stop"

        yield ChatResponse(
            content=delta_text,
            tool_calls=delta_tool_calls,
            finish_reason=mapped_finish,
            usage=usage,
        )
        if usage:
            emitted_usage = True

    if not emitted_usage:
        estimated = _estimate_chat_usage(
            messages,
            "".join(streamed_text_parts),
            provider=provider or PROVIDER,
            model=model,
            details={"source": "estimate_after_stream_without_provider_usage"},
        )
        yield ChatResponse(content="", finish_reason="stop", usage=estimated)


# ── Validation ────────────────────────────────────────────────────────


# ── FIM completion (tab-complete path) ────────────────────────────────

_FIM_SYSTEM_PROMPT = (
    "You are a fill-in-the-middle code completion engine. "
    "Return ONLY the missing middle code to insert at <MID>. "
    "No markdown fences, no explanations, no repeated prefix/suffix."
)


def _sanitize_fim_output(text: str) -> str:
    value = str(text or "")
    for tag in ("<MID>", "</MID>", "<PRE>", "</PRE>", "<SUF>", "</SUF>"):
        value = value.replace(tag, "")
    value = value.strip()
    if not value.startswith("```"):
        return value
    lines = value.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


_PROSE_MARKERS = (
    "it seems",
    "probably",
    "maybe",
    "the user",
    "we need",
    "could be",
    "looks like",
    "not enough context",
)


def _looks_like_insertable_code(text: str) -> bool:
    value = str(text or "").strip()
    if not value or len(value) > 400 or "\n\n" in value:
        return False
    lower = value.lower()
    if any(marker in lower for marker in _PROSE_MARKERS):
        return False
    if value.endswith("?") and not any(ch in value for ch in "@;=<"):
        return False
    return True


def _extract_sv_tail_from_reasoning(reasoning: str) -> str:
    """Best-effort SystemVerilog fragment from gpt-oss reasoning prose."""
    if not reasoning:
        return ""
    matches = re.findall(
        r"((?:if\s*\([^)]*\)\s*)?(?:\w[\w.]*(?:\[[^\]]*\])?\s*(?:<=|\+=|-=|=).+))",
        reasoning,
        flags=re.IGNORECASE,
    )
    for candidate in reversed(matches):
        cleaned = candidate.strip().rstrip(".,;")
        if cleaned and _looks_like_insertable_code(cleaned):
            return cleaned
    return ""


def _extract_fim_completion_text(message) -> str:
    """Prefer message.content; ignore gpt-oss reasoning prose unless it contains code."""
    if message is None:
        return ""
    content = str(getattr(message, "content", None) or "").strip()
    if content and _looks_like_insertable_code(content):
        return content

    reasoning = str(
        getattr(message, "reasoning_content", None)
        or getattr(message, "reasoning", None)
        or ""
    ).strip()
    if not reasoning:
        return content

    for match in re.finditer(r"`([^`\n]+)`", reasoning):
        candidate = match.group(1).strip()
        if _looks_like_insertable_code(candidate):
            return candidate

    for line in reasoning.splitlines():
        stripped = line.strip()
        if _looks_like_insertable_code(stripped):
            return stripped

    tail = _extract_sv_tail_from_reasoning(reasoning)
    if tail:
        return tail

    return content


def _complete_fim_openai_chat(
    prompt: str,
    *,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout_sec: float,
    base_url: Optional[str],
    provider: str,
    extra_body: Optional[dict] = None,
) -> tuple[str, Optional[TokenUsage]]:
    client = _get_openai_client(base_url, provider)
    kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": _FIM_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "timeout": timeout_sec,
    }
    _add_openai_temperature(
        kwargs,
        provider=provider,
        model=model,
        temperature=temperature,
    )
    _add_openai_token_limit(
        kwargs,
        provider=provider,
        model=model,
        max_tokens=max_tokens,
    )
    merged_extra = {**COMPLETION_EXTRA_BODY, **(extra_body or {})}
    # gpt-oss high reasoning: 60–120s latency, empty content — unusable for ghost text
    if "gpt-oss" in model.lower():
        merged_extra["reasoning_effort"] = "low"
    if merged_extra:
        kwargs["extra_body"] = merged_extra
    completion = client.chat.completions.create(**kwargs)
    if not completion.choices:
        payload = completion.model_dump() if hasattr(completion, "model_dump") else {}
        if payload.get("Output"):
            raise RuntimeError(
                "Bedrock chat completions failed (UnknownOperationException). "
                f"Use BEDROCK_API_BASE=https://bedrock-mantle.{BEDROCK_REGION}.api.aws/v1"
            )
        raise RuntimeError("Chat completion returned no choices")
    message = completion.choices[0].message if completion.choices else None
    text = _extract_fim_completion_text(message)
    usage = _usage_from_openai(
        completion.usage,
        provider=provider,
        model=model,
        details={"source": "completion_fim_chat"},
    )
    return _sanitize_fim_output(text), usage


def _complete_fim_openai_legacy(
    prompt: str,
    *,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout_sec: float,
    base_url: Optional[str],
    provider: str,
) -> tuple[str, Optional[TokenUsage]]:
    client = _get_openai_client(base_url, provider)
    completion = client.completions.create(
        model=model,
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout_sec,
    )
    text = completion.choices[0].text if completion.choices else ""
    usage = _usage_from_openai(
        completion.usage,
        provider=provider,
        model=model,
        details={"source": "completion_fim_legacy"},
    )
    return _sanitize_fim_output(text or ""), usage


def complete_fim_sync(
    prompt: str,
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: int = 64,
    timeout_sec: float = 5.0,
    base_url: Optional[str] = None,
) -> tuple[str, Optional[TokenUsage]]:
    """
    Fill-in-the-middle completion via OpenAI-compatible legacy completions API.
    Returns (generated_text, token_usage).
    """
    resolved_provider = _normalize_provider(
        provider or os.getenv("CHIPVERIFY_COMPLETION_PROVIDER") or PROVIDER
    )
    resolved_model = _resolve_fim_model_name(
        model or os.getenv("CHIPVERIFY_COMPLETION_MODEL"),
        resolved_provider,
    )
    resolved_base = base_url or _resolve_openai_base_url(resolved_provider)

    if resolved_provider == "convex_gateway":
        response = _chat_with_tools_convex_gateway(
            [
                {"role": "system", "content": _FIM_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            [],
            resolved_model,
            temperature,
            max_tokens,
            "none",
            {"chipverify_source": "completion_fim"},
        )
        return _sanitize_fim_output(response.content), response.usage

    if resolved_provider == "gemini":
        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=_FIM_SYSTEM_PROMPT,
        )
        client = _get_gemini_fim_client(timeout_sec)
        try:
            response = client.models.generate_content(
                model=_normalize_gemini_model_name(resolved_model),
                contents=prompt,
                config=config,
            )
        except Exception as exc:
            message = str(exc).lower()
            if "429" in message or "resource_exhausted" in message or "quota" in message:
                raise RuntimeError(
                    "Gemini rate limit exceeded — tab completion paused. Retry in ~1 min or upgrade quota."
                ) from exc
            raise
        text = getattr(response, "text", None) or ""
        usage = _usage_from_gemini(
            getattr(response, "usage_metadata", None),
            provider=resolved_provider,
            model=resolved_model,
        )
        return _sanitize_fim_output(text), usage

    if resolved_provider in ("nim", "bedrock"):
        fim_model = resolved_model
        fallback_model = os.getenv(
            "CHIPVERIFY_COMPLETION_FALLBACK_MODEL",
            os.getenv("BEDROCK_COMPLETION_FALLBACK_MODEL", "deepseek.v3.2"),
        ).strip()
        # gpt-oss on Bedrock mantle returns prose in `reasoning`, not code in `content`.
        if (
            resolved_provider == "bedrock"
            and "gpt-oss" in fim_model.lower()
            and fallback_model
        ):
            logger.info(
                "Tab completion: using %s (gpt-oss FIM has no code content on Bedrock)",
                fallback_model,
            )
            fim_model = fallback_model

        return _complete_fim_openai_chat(
            prompt,
            model=fim_model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_sec=timeout_sec,
            base_url=resolved_base,
            provider=resolved_provider,
        )

    try:
        return _complete_fim_openai_legacy(
            prompt,
            model=resolved_model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_sec=timeout_sec,
            base_url=resolved_base,
            provider=resolved_provider,
        )
    except Exception as exc:
        message = str(exc).lower()
        if "404" in message or "not found" in message:
            logger.info(
                "Legacy FIM completions unavailable for %s; falling back to chat",
                resolved_provider,
            )
            return _complete_fim_openai_chat(
                prompt,
                model=resolved_model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_sec=timeout_sec,
                base_url=resolved_base,
                provider=resolved_provider,
            )
        raise


def complete_fim_stream(
    prompt: str,
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: int = 64,
    timeout_sec: float = 5.0,
    base_url: Optional[str] = None,
):
    """Yield incremental FIM completion text chunks."""
    resolved_provider = _normalize_provider(
        provider or os.getenv("CHIPVERIFY_COMPLETION_PROVIDER") or PROVIDER
    )
    resolved_model = _resolve_fim_model_name(
        model or os.getenv("CHIPVERIFY_COMPLETION_MODEL"),
        resolved_provider,
    )
    resolved_base = base_url or _resolve_openai_base_url(resolved_provider)

    if resolved_provider == "convex_gateway":
        text, _usage = complete_fim_sync(
            prompt,
            model=resolved_model,
            provider=resolved_provider,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_sec=timeout_sec,
            base_url=resolved_base,
        )
        if text:
            yield text
        return

    if resolved_provider == "gemini":
        text, _usage = complete_fim_sync(
            prompt,
            model=resolved_model,
            provider=resolved_provider,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_sec=timeout_sec,
            base_url=resolved_base,
        )
        if text:
            yield text
        return

    if resolved_provider in ("nim", "bedrock"):
        text, _usage = complete_fim_sync(
            prompt,
            model=resolved_model,
            provider=resolved_provider,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_sec=timeout_sec,
            base_url=resolved_base,
        )
        if text:
            yield text
        return

    client = _get_openai_client(resolved_base, resolved_provider)
    try:
        stream = client.completions.create(
            model=resolved_model,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout_sec,
            stream=True,
        )
    except Exception as exc:
        message = str(exc).lower()
        if "404" not in message and "not found" not in message:
            raise
        text, _usage = complete_fim_sync(
            prompt,
            model=resolved_model,
            provider=resolved_provider,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_sec=timeout_sec,
            base_url=resolved_base,
        )
        if text:
            yield text
        return

    for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        if not choice:
            continue
        delta = getattr(choice, "text", None)
        if delta:
            yield delta


def validate_provider() -> tuple[bool, str]:
    """Check if the configured provider has valid credentials."""
    provider = PROVIDER
    if provider == "gemini":
        if not GOOGLE_API_KEY:
            return False, "GOOGLE_API_KEY or GEMINI_API_KEY not set"
        return True, "Gemini configured"
    elif provider == "azure_openai":
        if not _resolve_openai_base_url(provider):
            return False, "AZURE_OPENAI_ENDPOINT or CHIPVERIFY_LLM_BASE_URL not set"
        if not AZURE_OPENAI_USE_AAD and not (AZURE_OPENAI_API_KEY or OPENAI_API_KEY):
            return False, "AZURE_OPENAI_API_KEY or CHIPVERIFY_LLM_API_KEY not set"
        return True, "Azure OpenAI-compatible provider configured"
    elif provider == "bedrock":
        if not BEDROCK_API_KEY:
            return False, "BEDROCK_API_KEY or AWS_BEARER_TOKEN_BEDROCK not set"
        return True, f"Bedrock configured ({BEDROCK_REGION}, {BEDROCK_DEFAULT_MODEL})"
    elif provider == "convex_gateway":
        if not CONVEX_LLM_GATEWAY_URL:
            return False, "CHIPVERIFY_CONVEX_SITE_URL or CHIPVERIFY_CONVEX_LLM_GATEWAY_URL not set"
        return True, f"Convex gateway configured ({CONVEX_LLM_GATEWAY_URL})"
    elif provider in ("openai", "nim"):
        if not OPENAI_API_KEY:
            key_name = "NIM_API_KEY" if provider == "nim" else "OPENAI_API_KEY"
            return False, f"{key_name} not set"
        return True, f"{provider.upper()} configured"
    else:
        return False, (
            f"Unknown provider: {provider}. Use 'gemini', 'bedrock', 'openai', 'nim', "
            "'azure_openai', or 'convex_gateway'"
        )
