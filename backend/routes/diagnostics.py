import importlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from original_core import config
from original_core.core.ai_client import AIClient

router = APIRouter(prefix="/api/v1", tags=["assistant-diagnostics"])


class LlmRuntimeConfigRequest(BaseModel):
    provider: str = Field("azure_openai", description="bedrock, gemini, openai, nim, azure_openai, or convex_gateway")
    endpoint: str = Field(
        "",
        description="Provider base URL / endpoint. Leave blank to use the provider default.",
    )
    deployment: str = Field("", description="Azure deployment or model name")
    api_key: str = Field("", description="Provider API key")
    api_style: str = Field("deployment", description="v1 or deployment")
    auth_header: str = Field("api-key", description="bearer, api-key, or both")
    api_version: str = Field("2024-10-21", description="Azure API version for deployment style")


def _mask_secret(value: str | None) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 10:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


def _runtime_env_path() -> Path:
    explicit = os.getenv("CHIPVERIFY_RUNTIME_ENV_PATH")
    if explicit:
        path = Path(explicit)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    # The backend process loads `.env` from its backend runtime directory.
    # Do not write this file to CHIPVERIFY_WORKSPACE_ROOT, which is usually
    # the repo/workspace root and is not read when uvicorn starts from backend/.
    backend_root = Path(__file__).resolve().parents[1]
    backend_root.mkdir(parents=True, exist_ok=True)
    return backend_root / ".env"


def _read_env_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()


def _write_env_values(path: Path, values: dict[str, str]) -> None:
    existing = _read_env_lines(path)
    seen: set[str] = set()
    out: list[str] = []

    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)

    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")

    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _apply_runtime_llm_config(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ[key] = value

    # Refresh modules that read provider settings at import time.
    import llm_provider
    import original_core.config as original_config

    importlib.reload(llm_provider)
    importlib.reload(original_config)


def _default_endpoint_for_provider(provider: str) -> str:
    if provider == "bedrock":
        region = os.getenv("BEDROCK_REGION", "us-east-1").strip() or "us-east-1"
        return f"https://bedrock-mantle.{region}.api.aws/v1"
    if provider == "azure_openai":
        return "https://chipix-resource.services.ai.azure.com/openai/v1"
    if provider == "openai":
        return "https://api.openai.com/v1"
    if provider == "nim":
        return "https://integrate.api.nvidia.com/v1"
    if provider == "convex_gateway":
        return (
            os.getenv("CHIPVERIFY_CONVEX_LLM_GATEWAY_URL")
            or f"{os.getenv('CHIPVERIFY_CONVEX_SITE_URL', 'https://next-swordfish-62.convex.site').rstrip('/')}/api/llm/generate"
        )
    return ""


def _default_model_for_provider(provider: str) -> str:
    if provider == "bedrock":
        return os.getenv("BEDROCK_MODEL", "deepseek.v3.2")
    if provider == "azure_openai":
        return "DeepSeek-V4-Flash"
    if provider == "gemini":
        return "gemini-2.5-pro"
    if provider == "nim":
        return "meta/llama-3.1-70b-instruct"
    if provider == "openai":
        return "gpt-5.4"
    if provider == "convex_gateway":
        return os.getenv("MODEL_NAME", "gpt-5.4")
    return "chipix-v0.1"


def _provider_snapshot() -> dict[str, Any]:
    client = AIClient()
    return {
        "provider": client.provider,
        "model": client.model_alias,
        "base_url": client.base_url,
        "configured": client.is_generation_configured(),
        "api_key_present": bool(client.api_key),
        "api_key_mask": _mask_secret(client.api_key),
        "auth_header": getattr(config, "AZURE_OPENAI_AUTH_HEADER", ""),
        "api_style": getattr(config, "AZURE_OPENAI_API_STYLE", ""),
        "api_version": getattr(config, "AZURE_OPENAI_API_VERSION", ""),
    }


@router.get("/assistant/health")
def assistant_health():
    client = AIClient()
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "llm": {
            "provider": client.provider,
            "model": client.model_alias,
            "allow_local_provider": bool(
                getattr(client, "allow_local_provider", False)
            ),
            "configured": client.is_generation_configured(),
            "api_key_present": bool(client.api_key),
            "provider_env": os.getenv(
                "CHIPVERIFY_LLM_PROVIDER", os.getenv("MODEL_PROVIDER", "")
            ),
        },
        "ws": {
            "message_timeout_seconds": int(
                os.getenv("CHIPVERIFY_WS_MESSAGE_TIMEOUT_SECONDS", "300")
            ),
            "request_track_ttl_seconds": int(
                os.getenv("CHIPVERIFY_WS_REQUEST_TRACK_TTL_SECONDS", "900")
            ),
            "rate_limit_window_seconds": int(
                os.getenv("CHIPVERIFY_WS_RATE_LIMIT_WINDOW_SECONDS", "60")
            ),
            "rate_limit_messages_per_window": int(
                os.getenv("CHIPVERIFY_WS_RATE_LIMIT_MESSAGES_PER_WINDOW", "120")
            ),
        },
        "defaults": {
            "gemini_model": config.GEMINI_MODEL,
            "llm_timeout_seconds": config.LLM_TIMEOUT_SECONDS,
        },
    }


@router.get("/assistant/llm-config")
def get_llm_runtime_config():
    """Return non-secret LLM runtime configuration for desktop diagnostics."""
    return {
        "status": "ok",
        "persisted_env_path": str(_runtime_env_path()),
        "llm": _provider_snapshot(),
    }


@router.post("/assistant/llm-config")
def set_llm_runtime_config(request: LlmRuntimeConfigRequest):
    """Set local desktop LLM config without rebuilding or editing files manually."""
    provider = (request.provider or "azure_openai").strip().lower().replace("-", "_")
    if provider in {"openai_sdk", "chatgpt"}:
        provider = "openai"
    if provider in {"convex", "convex_cloud", "convex_llm"}:
        provider = "convex_gateway"
    if provider not in {"azure_openai", "bedrock", "gemini", "openai", "nim", "local", "convex_gateway"}:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {request.provider}")

    api_key = (request.api_key or "").strip()
    endpoint = ((request.endpoint or "").strip().rstrip("/") or _default_endpoint_for_provider(provider))
    deployment = (request.deployment or "").strip() or _default_model_for_provider(provider)
    api_style = (request.api_style or "deployment").strip().lower()
    auth_header = (request.auth_header or "api-key").strip().lower()
    api_version = (request.api_version or "2024-10-21").strip()

    values = {
        "MODEL_PROVIDER": provider,
        "CHIPVERIFY_LLM_PROVIDER": provider,
        "MODEL_NAME": deployment,
        "CHIPVERIFY_LLM_MODEL_ALIAS": deployment,
        "CHIPVERIFY_LLM_API_KEY_REQUIRED": "true",
    }

    if provider == "bedrock":
        region = os.getenv("BEDROCK_REGION", "us-east-1").strip() or "us-east-1"
        values.update(
            {
                "BEDROCK_REGION": region,
                "BEDROCK_MODEL": deployment,
                "BEDROCK_API_BASE": endpoint,
                "CHIPVERIFY_LLM_BASE_URL": endpoint,
                "CHIPVERIFY_COMPLETION_PROVIDER": "bedrock",
                "BEDROCK_COMPLETION_MODEL": deployment,
                "CHIPVERIFY_COMPLETION_MODEL": deployment,
            }
        )
        if api_key:
            values["BEDROCK_API_KEY"] = api_key
    elif provider == "azure_openai":
        values.update(
            {
                "AZURE_OPENAI_ENDPOINT": endpoint,
                "AZURE_OPENAI_DEPLOYMENT": deployment,
                "AZURE_OPENAI_API_STYLE": api_style,
                "AZURE_OPENAI_AUTH_HEADER": auth_header,
                "AZURE_OPENAI_API_VERSION": api_version,
                "CHIPVERIFY_LLM_BASE_URL": endpoint,
            }
        )
        if api_key:
            values.update(
                {
                    "AZURE_OPENAI_API_KEY": api_key,
                    "CHIPVERIFY_LLM_API_KEY": api_key,
                }
            )
    elif provider == "gemini":
        values.update(
            {
                "GEMINI_MODEL": deployment,
            }
        )
        if api_key:
            values.update(
                {
                    "GOOGLE_API_KEY": api_key,
                    "GEMINI_API_KEY": api_key,
                }
            )
    elif provider == "openai":
        values.update(
            {
                "OPENAI_API_BASE": endpoint,
                "OPENAI_BASE_URL": endpoint,
                "CHIPVERIFY_OPENAI_BASE_URL": endpoint,
                "CHIPVERIFY_LLM_BASE_URL": endpoint,
            }
        )
        if api_key:
            values.update(
                {
                    "OPENAI_API_KEY": api_key,
                    "CHIPVERIFY_OPENAI_API_KEY": api_key,
                    "CHIPVERIFY_LLM_API_KEY": api_key,
                }
            )
    elif provider == "convex_gateway":
        values.update(
            {
                "CHIPVERIFY_CONVEX_LLM_GATEWAY_URL": endpoint,
                "CHIPVERIFY_CONVEX_SITE_URL": endpoint.replace("/api/llm/generate", "").rstrip("/"),
                "CHIPVERIFY_LLM_API_KEY_REQUIRED": "false",
            }
        )
    else:
        values.update(
            {
                "CHIPVERIFY_LLM_BASE_URL": endpoint,
            }
        )
        if api_key:
            values.update(
                {
                    "OPENAI_API_KEY": api_key,
                    "CHIPVERIFY_LLM_API_KEY": api_key,
                }
            )

    env_path = _runtime_env_path()
    _write_env_values(env_path, values)
    _apply_runtime_llm_config(values)

    return {
        "status": "saved",
        "persisted_env_path": str(env_path),
        "llm": _provider_snapshot(),
    }


@router.post("/assistant/llm-config/test")
async def test_llm_runtime_config(request: LlmRuntimeConfigRequest):
    """Apply config and perform a tiny provider call for setup validation."""
    set_llm_runtime_config(request)
    try:
        client = AIClient()
        result = await client.generate(
            "Reply with exactly: ok",
            system_prompt="You are a provider configuration smoke test.",
            temperature=0,
            max_tokens=16,
        )
    except Exception:
        logger.exception("LLM provider smoke test failed")
        raise HTTPException(status_code=400, detail="LLM provider smoke test failed")

    return {
        "status": "ok",
        "llm": _provider_snapshot(),
        "sample": str(result or "").strip()[:80],
    }
