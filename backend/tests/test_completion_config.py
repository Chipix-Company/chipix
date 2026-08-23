"""Completion config provider auto-detection."""

from __future__ import annotations

import os

import pytest

from services.completion.config import load_completion_config


@pytest.mark.parametrize(
    "env,expected",
    [
        ({"CHIPVERIFY_COMPLETION_PROVIDER": "nim"}, "nim"),
        ({"CHIPVERIFY_COMPLETION_PROVIDER": "bedrock"}, "bedrock"),
        ({}, "bedrock"),
        ({"BEDROCK_API_KEY": "test-key"}, "bedrock"),
        ({"NIM_API_KEY": "test-key"}, "nim"),
        ({"GOOGLE_API_KEY": "test-key"}, "gemini"),
    ],
)
def test_resolve_completion_provider(monkeypatch, env, expected):
    for key in (
        "CHIPVERIFY_COMPLETION_PROVIDER",
        "CHIPVERIFY_COMPLETION_TIMEOUT_SEC",
        "BEDROCK_API_KEY",
        "AWS_BEARER_TOKEN_BEDROCK",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "NIM_API_KEY",
        "CHIPVERIFY_LLM_API_KEY",
        "OPENAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    for key, value in env.items():
        monkeypatch.setenv(key, value)

    cfg = load_completion_config()
    assert cfg.provider == expected
    if expected == "bedrock":
        assert cfg.timeout_sec == 12.0
    else:
        assert cfg.timeout_sec == 3.0


def test_bedrock_default_model(monkeypatch):
    for key in (
        "CHIPVERIFY_COMPLETION_PROVIDER",
        "CHIPVERIFY_COMPLETION_MODEL",
        "CHIPVERIFY_COMPLETION_TIMEOUT_SEC",
        "BEDROCK_MODEL",
        "BEDROCK_COMPLETION_MODEL",
        "CHIPVERIFY_BEDROCK_MODEL",
        "CHIPVERIFY_BEDROCK_COMPLETION_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CHIPVERIFY_COMPLETION_PROVIDER", "bedrock")

    cfg = load_completion_config()
    assert cfg.provider == "bedrock"
    assert cfg.model == "openai.gpt-oss-20b"
