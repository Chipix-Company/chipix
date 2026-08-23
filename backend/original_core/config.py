"""
Configuration for ChipVerify AI local-first on-prem runtime.

Primary production target is Linux servers, with Windows support for local testing.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


from common.env import env_bool as _env_bool
def _normalize_provider(name: str) -> str:
    value = (name or "gemini").strip().lower().replace("-", "_")
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


# LLM provider contract with compatibility aliases for test environments.
LLM_PROVIDER: str = _normalize_provider(
    os.getenv(
        "CHIPVERIFY_LLM_PROVIDER",
        os.getenv("MODEL_PROVIDER", "gemini"),
    )
)

ALLOW_LOCAL_LLM: bool = _env_bool("CHIPVERIFY_ALLOW_LOCAL_LLM", False)

AZURE_OPENAI_ENDPOINT: str = os.getenv(
    "AZURE_OPENAI_ENDPOINT",
    os.getenv("AZURE_OPENAI_BASE_URL", os.getenv("AZURE_AI_OPENAI_ENDPOINT", "")),
).rstrip("/")
AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_DEPLOYMENT: str = os.getenv(
    "AZURE_OPENAI_DEPLOYMENT",
    os.getenv("AZURE_OPENAI_MODEL", "DeepSeek-V4-Flash"),
)
AZURE_OPENAI_USE_AAD: bool = _env_bool("AZURE_OPENAI_USE_AAD", False)
AZURE_OPENAI_TOKEN_SCOPE: str = os.getenv(
    "AZURE_OPENAI_TOKEN_SCOPE",
    "https://ai.azure.com/.default",
)
AZURE_OPENAI_AUTH_HEADER: str = os.getenv(
    "AZURE_OPENAI_AUTH_HEADER",
    "api-key",
).strip().lower()
AZURE_OPENAI_API_STYLE: str = os.getenv(
    "AZURE_OPENAI_API_STYLE",
    "deployment",
).strip().lower()
AZURE_OPENAI_API_VERSION: str = os.getenv(
    "AZURE_OPENAI_API_VERSION",
    "2024-10-21",
).strip()

_default_base_url = "http://127.0.0.1:7349/v1"
if LLM_PROVIDER == "nim":
    _default_base_url = os.getenv(
        "NIM_API_BASE",
        os.getenv(
            "OPENAI_API_BASE",
            os.getenv("OPENAI_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        ),
    )
elif LLM_PROVIDER == "openai":
    _default_base_url = os.getenv(
        "OPENAI_API_BASE",
        os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
elif LLM_PROVIDER == "azure_openai":
    _default_base_url = AZURE_OPENAI_ENDPOINT
elif LLM_PROVIDER == "bedrock":
    _bedrock_region = os.getenv("BEDROCK_REGION", "us-east-1").strip() or "us-east-1"
    _default_base_url = os.getenv(
        "BEDROCK_API_BASE",
        f"https://bedrock-mantle.{_bedrock_region}.api.aws/v1",
    )
elif LLM_PROVIDER == "convex_gateway":
    _default_base_url = os.getenv(
        "CHIPVERIFY_CONVEX_LLM_GATEWAY_URL",
        (
            os.getenv("CHIPVERIFY_CONVEX_SITE_URL", "https://next-swordfish-62.convex.site").rstrip("/")
            + "/api/llm/generate"
        ),
    )

_generic_base_url = os.getenv("CHIPVERIFY_LLM_BASE_URL", "").strip()
if LLM_PROVIDER == "azure_openai":
    # Provider-specific settings must win when switching from another
    # OpenAI-compatible backend. Otherwise a persisted Bedrock/NIM generic
    # alias can silently route Azure requests to the wrong service.
    LLM_BASE_URL = (AZURE_OPENAI_ENDPOINT or _generic_base_url or _default_base_url).rstrip("/")
elif LLM_PROVIDER == "bedrock":
    LLM_BASE_URL = (
        os.getenv("BEDROCK_API_BASE")
        or _generic_base_url
        or _default_base_url
    ).rstrip("/")
elif LLM_PROVIDER == "nim":
    LLM_BASE_URL = (
        os.getenv("NIM_API_BASE")
        or os.getenv("OPENAI_API_BASE")
        or os.getenv("OPENAI_BASE_URL")
        or _generic_base_url
        or _default_base_url
    ).rstrip("/")
elif LLM_PROVIDER == "openai":
    LLM_BASE_URL = (
        os.getenv("OPENAI_API_BASE")
        or os.getenv("OPENAI_BASE_URL")
        or _generic_base_url
        or _default_base_url
    ).rstrip("/")
else:
    LLM_BASE_URL = (_generic_base_url or _default_base_url).rstrip("/")

if LLM_PROVIDER == "azure_openai":
    _default_model_alias = AZURE_OPENAI_DEPLOYMENT
elif LLM_PROVIDER == "openai":
    _default_model_alias = os.getenv(
        "OPENAI_MODEL",
        os.getenv("MODEL_NAME", "gpt-5.4"),
    )
elif LLM_PROVIDER == "nim":
    _default_model_alias = os.getenv(
        "NIM_MODEL",
        os.getenv("MODEL_NAME", "meta/llama-3.1-70b-instruct"),
    )
elif LLM_PROVIDER == "convex_gateway":
    _default_model_alias = os.getenv("MODEL_NAME", "gpt-5.4")
elif LLM_PROVIDER == "bedrock":
    _default_model_alias = (
        os.getenv("BEDROCK_MODEL")
        or os.getenv("CHIPVERIFY_BEDROCK_MODEL")
        or os.getenv("MODEL_NAME", "deepseek.v3.2")
    )
else:
    _default_model_alias = os.getenv("MODEL_NAME", "moonshotai/kimi-k2-instruct")

_generic_model_alias = os.getenv("CHIPVERIFY_LLM_MODEL_ALIAS", "").strip()
if LLM_PROVIDER == "azure_openai":
    LLM_MODEL_ALIAS = AZURE_OPENAI_DEPLOYMENT or _generic_model_alias or _default_model_alias
elif LLM_PROVIDER == "bedrock":
    LLM_MODEL_ALIAS = (
        os.getenv("BEDROCK_MODEL")
        or os.getenv("CHIPVERIFY_BEDROCK_MODEL")
        or _generic_model_alias
        or _default_model_alias
    )
elif LLM_PROVIDER == "nim":
    LLM_MODEL_ALIAS = (
        os.getenv("NIM_MODEL")
        or _generic_model_alias
        or _default_model_alias
    )
elif LLM_PROVIDER == "openai":
    LLM_MODEL_ALIAS = (
        os.getenv("OPENAI_MODEL")
        or _generic_model_alias
        or _default_model_alias
    )
else:
    LLM_MODEL_ALIAS = _generic_model_alias or _default_model_alias

if LLM_PROVIDER == "openai" and "gemini" in LLM_MODEL_ALIAS.lower():
    LLM_MODEL_ALIAS = os.getenv("OPENAI_MODEL", "gpt-5.4")

if LLM_PROVIDER == "azure_openai":
    _default_llm_api_key = os.getenv("AZURE_OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
elif LLM_PROVIDER == "nim":
    _default_llm_api_key = os.getenv("NIM_API_KEY", os.getenv("OPENAI_API_KEY", ""))
elif LLM_PROVIDER == "openai":
    _default_llm_api_key = os.getenv(
        "OPENAI_API_KEY",
        os.getenv("CHIPVERIFY_OPENAI_API_KEY", ""),
    )
elif LLM_PROVIDER == "convex_gateway":
    _default_llm_api_key = ""
elif LLM_PROVIDER == "bedrock":
    _default_llm_api_key = os.getenv("BEDROCK_API_KEY", os.getenv("AWS_BEARER_TOKEN_BEDROCK", ""))
else:
    _default_llm_api_key = os.getenv("OPENAI_API_KEY", "")

_generic_llm_api_key = os.getenv("CHIPVERIFY_LLM_API_KEY", "")
if LLM_PROVIDER == "azure_openai":
    LLM_API_KEY = AZURE_OPENAI_API_KEY or _generic_llm_api_key or _default_llm_api_key
elif LLM_PROVIDER == "bedrock":
    LLM_API_KEY = (
        os.getenv("BEDROCK_API_KEY")
        or os.getenv("AWS_BEARER_TOKEN_BEDROCK")
        or _generic_llm_api_key
        or _default_llm_api_key
    )
elif LLM_PROVIDER == "nim":
    LLM_API_KEY = (
        os.getenv("NIM_API_KEY")
        or _generic_llm_api_key
        or _default_llm_api_key
    )
elif LLM_PROVIDER == "openai":
    LLM_API_KEY = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("CHIPVERIFY_OPENAI_API_KEY")
        or _generic_llm_api_key
        or _default_llm_api_key
    )
else:
    LLM_API_KEY = _generic_llm_api_key or _default_llm_api_key
LLM_API_KEY_REQUIRED: bool = _env_bool(
    "CHIPVERIFY_LLM_API_KEY_REQUIRED",
    False if LLM_PROVIDER == "convex_gateway" else True,
)
LLM_TIMEOUT_SECONDS: int = int(os.getenv("CHIPVERIFY_LLM_TIMEOUT_SECONDS", "300"))

# Optional legacy provider compatibility mode (temporary migration bridge).
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))
GEMINI_MODEL: str = os.getenv(
    "GEMINI_MODEL",
    os.getenv("CHIPVERIFY_GEMINI_MODEL", "gemini-2.5-pro"),
)

# ═══════════════════════════════════════════════════════════════════════
# LLM Settings
# ═══════════════════════════════════════════════════════════════════════

DEFAULT_TEMPERATURE: float = 0.7
# Keep output token budget conservative so prompt + output fit local runtime context.
DEFAULT_MAX_TOKENS: int = int(os.getenv("CHIPVERIFY_LLM_MAX_TOKENS", "1024"))
API_TIMEOUT_SECONDS: int = LLM_TIMEOUT_SECONDS

# Prompt shaping limits for structured generation calls.
PROMPT_MAX_CHARS: int = int(os.getenv("CHIPVERIFY_PROMPT_MAX_CHARS", "5200"))
PROMPT_BLOCK_MAX_CHARS: int = int(
    os.getenv("CHIPVERIFY_PROMPT_BLOCK_MAX_CHARS", "2200")
)

# ═══════════════════════════════════════════════════════════════════════
# Pipeline Settings
# ═══════════════════════════════════════════════════════════════════════

MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "10"))
MAX_FILE_GEN_RETRIES: int = 3  # Max retries per file if validation fails
OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "output")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ═══════════════════════════════════════════════════════════════════════
# Simulator Settings
# ═══════════════════════════════════════════════════════════════════════

DEFAULT_SIMULATOR: str = os.getenv("DEFAULT_SIMULATOR", "icarus")
SIMULATION_TIMEOUT: int = int(os.getenv("SIMULATION_TIMEOUT", "300"))

# ═══════════════════════════════════════════════════════════════════════
# Project Paths
# ═══════════════════════════════════════════════════════════════════════

PROJECT_ROOT: Path = Path(__file__).parent
TEMPLATES_DIR: Path = PROJECT_ROOT / "templates"

# ═══════════════════════════════════════════════════════════════════════
# File Generation Order (11 verification files)
# ═══════════════════════════════════════════════════════════════════════

VERIFICATION_FILES_ORDER: list[str] = [
    "interface.sv",
    "coverage.sv",
    "assertions.sv",
    "driver.sv",
    "monitor.sv",
    "scoreboard.sv",
    "sequences.sv",
    "environment.sv",
    "tb.sv",
    "Makefile",
    "test_plan.md",
]
