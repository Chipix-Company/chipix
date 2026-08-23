import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_CORE_ROOT = BACKEND_ROOT / "original_core"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(ORIGINAL_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(ORIGINAL_CORE_ROOT))

from routes import diagnostics


def test_bedrock_runtime_config_is_persisted(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    captured = {}
    monkeypatch.setattr(diagnostics, "_runtime_env_path", lambda: env_path)
    monkeypatch.setattr(
        diagnostics,
        "_apply_runtime_llm_config",
        lambda values: captured.update(values),
    )
    monkeypatch.setattr(
        diagnostics,
        "_provider_snapshot",
        lambda: {
            "provider": captured.get("CHIPVERIFY_LLM_PROVIDER"),
            "model": captured.get("BEDROCK_MODEL"),
        },
    )

    result = diagnostics.set_llm_runtime_config(
        diagnostics.LlmRuntimeConfigRequest(
            provider="bedrock",
            endpoint="https://bedrock-mantle.us-east-1.api.aws/v1",
            deployment="deepseek.v3.2",
            api_key="test-bedrock-key",
        )
    )

    persisted = env_path.read_text(encoding="utf-8")
    assert result["llm"]["provider"] == "bedrock"
    assert "CHIPVERIFY_LLM_PROVIDER=bedrock" in persisted
    assert "BEDROCK_MODEL=deepseek.v3.2" in persisted
    assert "BEDROCK_API_KEY=test-bedrock-key" in persisted
    assert "BEDROCK_API_BASE=https://bedrock-mantle.us-east-1.api.aws/v1" in persisted
