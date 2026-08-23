import json
import os
import subprocess
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_config_snapshot(overrides: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(overrides)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; "
                "from original_core import config; "
                "print(json.dumps({"
                "'provider': config.LLM_PROVIDER,"
                "'base_url': config.LLM_BASE_URL,"
                "'model': config.LLM_MODEL_ALIAS,"
                "'api_key': config.LLM_API_KEY"
                "}))"
            ),
        ],
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_azure_settings_override_stale_generic_bedrock_aliases():
    snapshot = _load_config_snapshot(
        {
            "CHIPVERIFY_LLM_PROVIDER": "azure_openai",
            "MODEL_PROVIDER": "azure_openai",
            "CHIPVERIFY_LLM_BASE_URL": "https://bedrock.example/v1",
            "CHIPVERIFY_LLM_API_KEY": "stale-bedrock-key",
            "CHIPVERIFY_LLM_MODEL_ALIAS": "deepseek.v3.2",
            "AZURE_OPENAI_ENDPOINT": "https://azure.example/openai/v1",
            "AZURE_OPENAI_API_KEY": "azure-key",
            "AZURE_OPENAI_DEPLOYMENT": "gpt-5.4",
        }
    )

    assert snapshot == {
        "provider": "azure_openai",
        "base_url": "https://azure.example/openai/v1",
        "model": "gpt-5.4",
        "api_key": "azure-key",
    }


def test_bedrock_settings_override_stale_generic_azure_aliases():
    snapshot = _load_config_snapshot(
        {
            "CHIPVERIFY_LLM_PROVIDER": "bedrock",
            "MODEL_PROVIDER": "bedrock",
            "CHIPVERIFY_LLM_BASE_URL": "https://azure.example/openai/v1",
            "CHIPVERIFY_LLM_API_KEY": "stale-azure-key",
            "CHIPVERIFY_LLM_MODEL_ALIAS": "gpt-5.4",
            "BEDROCK_API_BASE": "https://bedrock.example/v1",
            "BEDROCK_API_KEY": "bedrock-key",
            "BEDROCK_MODEL": "deepseek.v3.2",
        }
    )

    assert snapshot == {
        "provider": "bedrock",
        "base_url": "https://bedrock.example/v1",
        "model": "deepseek.v3.2",
        "api_key": "bedrock-key",
    }
