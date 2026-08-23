import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import llm_provider


def test_normalize_gemini_model_name_strips_models_prefix_and_suffix():
    normalized = llm_provider._normalize_gemini_model_name(
        "models/gemini-2.5-flash:generateContent"
    )
    assert normalized == "gemini-2.5-flash"


def test_normalize_gemini_model_name_handles_provider_prefix_alias():
    normalized = llm_provider._normalize_gemini_model_name("google/gemini-2.5-pro")
    assert normalized == "gemini-2.5-pro"


def test_normalize_gemini_model_name_strips_full_request_url():
    normalized = llm_provider._normalize_gemini_model_name(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=abc123"
    )
    assert normalized == "gemini-2.5-flash"


def test_normalize_gemini_model_name_strips_query_and_fragment_suffixes():
    normalized = llm_provider._normalize_gemini_model_name(
        "models/gemini-2.5-flash:countTokens?key=abc123#fragment"
    )
    assert normalized == "gemini-2.5-flash"


def test_resolve_model_name_uses_gemini_default_when_generic_model_is_non_gemini(
    monkeypatch,
):
    monkeypatch.setattr(llm_provider, "DEFAULT_MODEL", "z-ai/glm4.7")
    monkeypatch.setattr(llm_provider, "GEMINI_DEFAULT_MODEL", "gemini-2.5-flash")

    resolved = llm_provider._resolve_model_name(None, "gemini")
    assert resolved == "gemini-2.5-flash"


def test_resolve_model_name_normalizes_explicit_gemini_model():
    resolved = llm_provider._resolve_model_name(
        "models/gemini-2.5-pro:generateContent", "gemini"
    )
    assert resolved == "gemini-2.5-pro"


def test_resolve_model_name_normalizes_gemini_default_url(monkeypatch):
    monkeypatch.setattr(
        llm_provider,
        "GEMINI_DEFAULT_MODEL",
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=abc123",
    )
    monkeypatch.setattr(llm_provider, "DEFAULT_MODEL", "")

    resolved = llm_provider._resolve_model_name(None, "gemini")
    assert resolved == "gemini-2.5-flash"


def test_resolve_model_name_coerces_explicit_non_gemini_model_when_provider_is_gemini(
    monkeypatch,
):
    """Regression: when caller passes a non-Gemini model name (e.g. the
    agentic loop falling back to MODEL_NAME=z-ai/glm4.7) but provider is
    Gemini, we MUST coerce to GEMINI_DEFAULT_MODEL instead of shipping the
    bad name to the Gemini API (which 404s)."""
    monkeypatch.setattr(llm_provider, "GEMINI_DEFAULT_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(llm_provider, "DEFAULT_MODEL", "z-ai/glm4.7")

    resolved = llm_provider._resolve_model_name("z-ai/glm4.7", "gemini")
    assert resolved == "gemini-2.5-flash"


def test_resolve_model_name_falls_back_to_known_gemini_when_nothing_configured(
    monkeypatch,
):
    monkeypatch.setattr(llm_provider, "GEMINI_DEFAULT_MODEL", None)
    monkeypatch.setattr(llm_provider, "DEFAULT_MODEL", "")

    resolved = llm_provider._resolve_model_name(None, "gemini")
    assert resolved == llm_provider.GEMINI_FALLBACK_MODEL
    assert "gemini" in resolved


def test_resolve_model_name_ignores_gemini_model_when_provider_is_azure(monkeypatch):
    monkeypatch.setattr(llm_provider, "DEFAULT_MODEL", "gemini-2.5-pro")
    monkeypatch.setattr(llm_provider, "OPENAI_DEFAULT_MODEL", "DeepSeek-V4-Flash")
    monkeypatch.setattr(llm_provider, "AZURE_OPENAI_DEPLOYMENT", "DeepSeek-V4-Flash")

    resolved = llm_provider._resolve_model_name("gemini-2.5-pro", "azure_openai")
    assert resolved == "DeepSeek-V4-Flash"


def test_normalize_provider_accepts_openai_sdk_aliases():
    assert llm_provider._normalize_provider("openai-sdk") == "openai"
    assert llm_provider._normalize_provider("chatgpt") == "openai"


def test_normalize_provider_accepts_convex_aliases():
    assert llm_provider._normalize_provider("convex") == "convex_gateway"
    assert llm_provider._normalize_provider("convex-cloud") == "convex_gateway"


def test_resolve_model_name_ignores_gemini_model_when_provider_is_openai(monkeypatch):
    monkeypatch.setattr(llm_provider, "DEFAULT_MODEL", "gemini-2.5-pro")
    monkeypatch.setattr(llm_provider, "OPENAI_DEFAULT_MODEL", None)

    resolved = llm_provider._resolve_model_name("gemini-2.5-pro", "openai")
    assert resolved == "gpt-5.4"


def test_resolve_openai_base_url_uses_official_openai_default(monkeypatch):
    monkeypatch.setattr(llm_provider, "OPENAI_API_BASE", None)

    resolved = llm_provider._resolve_openai_base_url("openai")
    assert resolved == "https://api.openai.com/v1"


def test_openai_gpt5_uses_max_completion_tokens_and_omits_temperature():
    payload = {}

    llm_provider._add_openai_token_limit(
        payload,
        provider="openai",
        model="gpt-5.4",
        max_tokens=123,
    )
    llm_provider._add_openai_temperature(
        payload,
        provider="openai",
        model="gpt-5.4",
        temperature=0.3,
    )

    assert payload == {"max_completion_tokens": 123}


def test_azure_gpt5_uses_max_completion_tokens_and_omits_temperature():
    payload = {}

    llm_provider._add_openai_token_limit(
        payload,
        provider="azure_openai",
        model="gpt-5.4",
        max_tokens=123,
    )
    llm_provider._add_openai_temperature(
        payload,
        provider="azure_openai",
        model="gpt-5.4",
        temperature=0.3,
    )

    assert payload == {"max_completion_tokens": 123}


def test_openai_legacy_model_keeps_max_tokens_and_temperature():
    payload = {}

    llm_provider._add_openai_token_limit(
        payload,
        provider="openai",
        model="gpt-4o-mini",
        max_tokens=123,
    )
    llm_provider._add_openai_temperature(
        payload,
        provider="openai",
        model="gpt-4o-mini",
        temperature=0.3,
    )

    assert payload == {"max_tokens": 123, "temperature": 0.3}


def test_convex_gateway_validate_provider_does_not_require_provider_key(monkeypatch):
    monkeypatch.setattr(llm_provider, "PROVIDER", "convex_gateway")
    monkeypatch.setattr(llm_provider, "CONVEX_LLM_GATEWAY_URL", "https://example.convex.site/api/llm/generate")

    ok, message = llm_provider.validate_provider()

    assert ok is True
    assert "Convex gateway configured" in message


def test_convex_gateway_generate_uses_gateway_response(monkeypatch):
    monkeypatch.setattr(
        llm_provider,
        "_convex_gateway_post",
        lambda payload: {
            "ok": True,
            "content": "hello",
            "reasoning": "thought",
            "usage": {
                "inputTokens": 4,
                "outputTokens": 2,
                "totalTokens": 6,
                "provider": "openai",
                "model": "gpt-5.4",
            },
        },
    )

    content, reasoning = llm_provider.generate(
        "sys",
        "user",
        provider="convex_gateway",
        model="gpt-5.4",
    )

    assert content == "hello"
    assert reasoning == "thought"


def test_convex_gateway_chat_maps_tool_calls(monkeypatch):
    monkeypatch.setattr(
        llm_provider,
        "_convex_gateway_post",
        lambda payload: {
            "ok": True,
            "content": "",
            "finishReason": "tool_calls",
            "toolCalls": [
                {"id": "call_1", "name": "inspect_file", "arguments": {"path": "a.sv"}},
            ],
            "usage": {
                "inputTokens": 10,
                "outputTokens": 1,
                "totalTokens": 11,
                "provider": "openai",
                "model": "gpt-5.4",
            },
        },
    )

    response = llm_provider.chat_with_tools(
        [{"role": "user", "content": "inspect"}],
        [],
        provider="convex_gateway",
        model="gpt-5.4",
    )

    assert response.finish_reason == "tool_calls"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "inspect_file"
    assert response.tool_calls[0].arguments == {"path": "a.sv"}
    assert response.usage.total_tokens == 11


def test_openai_compatible_nim_keeps_legacy_token_field_even_with_gpt_name():
    payload = {}

    llm_provider._add_openai_token_limit(
        payload,
        provider="nim",
        model="gpt-5.4",
        max_tokens=123,
    )
    llm_provider._add_openai_temperature(
        payload,
        provider="nim",
        model="gpt-5.4",
        temperature=0.3,
    )

    assert payload == {"max_tokens": 123, "temperature": 0.3}
