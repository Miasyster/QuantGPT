"""Tests for LLM environment variable resolution."""

import pytest

from quantgpt.llm_service import get_llm_api_key, get_llm_base_url, get_llm_model

_ALL_VARS = [
    "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
    "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_when_nothing_is_set():
    assert get_llm_api_key() == ""
    assert get_llm_base_url() == "https://api.deepseek.com/v1"
    assert get_llm_model() == "deepseek-chat"


def test_deepseek_vars_still_work(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "old-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "old-model")
    assert get_llm_api_key() == "old-key"
    assert get_llm_base_url() == "https://old.example/v1"
    assert get_llm_model() == "old-model"


def test_llm_vars_take_precedence(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "old-key")
    monkeypatch.setenv("LLM_API_KEY", "new-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "old-model")
    monkeypatch.setenv("LLM_MODEL", "new-model")
    assert get_llm_api_key() == "new-key"
    assert get_llm_model() == "new-model"


def test_empty_llm_var_falls_back(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "old-key")
    assert get_llm_api_key() == "old-key"
