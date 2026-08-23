from __future__ import annotations

import sys
import importlib
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "original_core"))


def test_openai_client_requests_json_object_for_json_only_prompts():
    from original_core.core.ai_client import AIClient

    client = AIClient.__new__(AIClient)
    client.provider = "openai"
    client._use_azure_deployment_api = lambda: False

    messages = [
        {"role": "system", "content": "Return valid JSON only."},
        {"role": "user", "content": "Return exactly one JSON object."},
    ]

    assert client._should_request_openai_json_object(messages)


def test_azure_openai_v1_client_requests_json_object_for_json_only_prompts():
    from original_core.core.ai_client import AIClient

    client = AIClient.__new__(AIClient)
    client.provider = "azure_openai"
    client._use_azure_deployment_api = lambda: False

    messages = [
        {"role": "system", "content": "Return valid JSON only."},
        {"role": "user", "content": "Return exactly one JSON object."},
    ]

    assert client._should_request_openai_json_object(messages)


def test_azure_deployment_client_does_not_force_json_object_mode():
    from original_core.core.ai_client import AIClient

    client = AIClient.__new__(AIClient)
    client.provider = "azure_openai"
    client._use_azure_deployment_api = lambda: True

    messages = [
        {"role": "system", "content": "Return valid JSON only."},
        {"role": "user", "content": "Return exactly one JSON object."},
    ]

    assert not client._should_request_openai_json_object(messages)


def test_ai_client_accepts_bedrock_without_generic_llm_key(monkeypatch):
    for key in (
        "CHIPVERIFY_LLM_PROVIDER",
        "MODEL_PROVIDER",
        "CHIPVERIFY_LLM_API_KEY",
        "OPENAI_API_KEY",
        "CHIPVERIFY_OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "NIM_API_KEY",
        "AZURE_OPENAI_API_KEY",
            "BEDROCK_API_KEY",
            "BEDROCK_MODEL",
            "CHIPVERIFY_BEDROCK_MODEL",
            "CHIPVERIFY_LLM_MODEL_ALIAS",
            "MODEL_NAME",
        ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("CHIPVERIFY_LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("MODEL_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_API_KEY", "test-bedrock-key")
    monkeypatch.setenv("BEDROCK_MODEL", "deepseek.v3.2")
    monkeypatch.setenv("CHIPVERIFY_LLM_MODEL_ALIAS", "deepseek.v3.2")
    monkeypatch.setenv("MODEL_NAME", "deepseek.v3.2")

    sys.modules.pop("config", None)
    sys.modules.pop("original_core.core.ai_client", None)
    config = importlib.import_module("config")
    importlib.reload(config)
    ai_client = importlib.import_module("original_core.core.ai_client")
    importlib.reload(ai_client)

    client = ai_client.AIClient()

    assert client.provider == "bedrock"
    assert client.model_alias == "deepseek.v3.2"
    assert client.is_generation_configured()


def test_integration_candidate_can_reuse_existing_spec_requirements():
    from services.mental_model.builder import _sanitize_integration_candidate
    from services.mental_model.schema import DesignBlock, PortInfo

    design = DesignBlock(
        top_module="fifo",
        modules=["fifo"],
        ports=[
            PortInfo(name="clk", direction="input"),
            PortInfo(name="rst", direction="input"),
            PortInfo(name="empty", direction="output"),
        ],
        description="FIFO design",
    )

    candidate = {
        "top_description": "FIFO design",
        "requirements": [],
        "verification_intent": {
            "unit_tests": [
                {
                    "name": "reset_empty",
                    "description": "Check empty after reset",
                    "requirement_ids": ["REQ-001"],
                }
            ],
            "formal_properties": [
                {
                    "name": "empty_known",
                    "description": "empty should be known",
                    "related_signals": ["empty"],
                    "requirement_ids": ["REQ-001"],
                }
            ],
        },
    }

    cleaned, errors = _sanitize_integration_candidate(
        candidate,
        design,
        {"fifo": design},
        existing_req_ids={"REQ-001"},
    )

    assert cleaned is not None
    assert errors == []
    assert cleaned["verification_intent"]["unit_tests"][0]["requirement_ids"] == [
        "REQ-001"
    ]
