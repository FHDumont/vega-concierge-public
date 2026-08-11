"""`seed_providers_from_env()` — cascade bootstrap via OS tokens (F-BACKEND-3, Step B).

Test names use isolated specs (`TestSeedOpenAI`/`TestSeedClaude`/…) instead of the real names
("OpenAI"/"Claude"/"Bedrock"/"Ollama Local") — even with the test DB isolated from the real
`vega.db` (DT-036), keeping fictitious names avoids any collision with production providers if a
test runs against another DB by mistake."""
from __future__ import annotations

import pytest

from app.llm import llm_config
from app.settings import settings

TEST_CASCADE_SPECS = {
    "OPENAI": {
        "env_field": "openai_api_key",
        "name": "TestSeedOpenAI",
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "model_from_settings": "openai_chat_model",
    },
    "ANTHROPIC": {
        "env_field": "anthropic_api_key",
        "name": "TestSeedClaude",
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com",
        "model_from_settings": "anthropic_chat_model",
    },
    "BEDROCK": {
        "env_field": "aws_bearer_token_bedrock",
        "name": "TestSeedBedrock",
        "kind": "bedrock",
        "base_url": "",
        "model_from_settings": "bedrock_chat_model",
    },
    "OLLAMA": {
        "name": "TestSeedOllama",
        "kind": "openai",
        "model_from_settings": "ollama_chat_model",
    },
}
TEST_NAMES = [spec["name"] for spec in TEST_CASCADE_SPECS.values()]


def _by_name(name: str) -> dict | None:
    for p in llm_config.list_providers():
        if p["name"] == name:
            return p
    return None


def _current_key(provider_id: str) -> str:
    row = llm_config.get_provider_with_key(provider_id)
    return row["api_key"] if row else ""


@pytest.fixture
def seed_env(monkeypatch):
    """Test specs + zeroed tokens; cloud-only priority (Ollama comes in on tests that ask for it)."""
    llm_config.init_db()
    monkeypatch.setattr(llm_config, "_CASCADE_SPECS", TEST_CASCADE_SPECS)
    monkeypatch.setattr(settings, "llm_provider_priority", "BEDROCK,OPENAI,ANTHROPIC")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "aws_bearer_token_bedrock", "")
    monkeypatch.setattr(settings, "aws_default_region", "sa-east-1")
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:11434")
    yield
    for name in TEST_NAMES:
        row = _by_name(name)
        if row:
            llm_config.delete_provider(row["id"])


def test_no_token_is_a_noop(seed_env):
    result = llm_config.seed_providers_from_env()
    assert result == {"created": 0, "updated": 0, "ordered": 0}
    assert all(_by_name(n) is None for n in TEST_NAMES)


def test_creates_one_provider_per_token_present(seed_env, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider_priority", "OPENAI,ANTHROPIC")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-openai")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test-claude")

    result = llm_config.seed_providers_from_env()

    assert result == {"created": 2, "updated": 0, "ordered": 0}
    openai_row = _by_name("TestSeedOpenAI")
    claude_row = _by_name("TestSeedClaude")
    assert openai_row is not None and claude_row is not None
    assert openai_row["kind"] == "openai"
    assert openai_row["model"] == "gpt-4o-mini"
    assert openai_row["enabled"] is True
    assert claude_row["kind"] == "anthropic"
    assert claude_row["model"] == "claude-sonnet-4-5"
    assert openai_row["order"] == 0
    assert claude_row["order"] == 1
    assert _current_key(openai_row["id"]) == "sk-test-openai"
    assert _current_key(claude_row["id"]) == "sk-test-claude"
    assert _by_name("TestSeedBedrock") is None


def test_priority_skips_unconfigured_and_falls_through_to_ollama(seed_env, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider_priority", "BEDROCK,OPENAI,ANTHROPIC,OLLAMA")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-openai")

    result = llm_config.seed_providers_from_env()

    assert result["created"] == 2
    openai_row = _by_name("TestSeedOpenAI")
    ollama_row = _by_name("TestSeedOllama")
    assert openai_row is not None and ollama_row is not None
    assert openai_row["order"] == 0
    assert ollama_row["order"] == 1
    assert _by_name("TestSeedBedrock") is None


def test_bedrock_resolves_base_url_from_aws_default_region(seed_env, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider_priority", "BEDROCK")
    monkeypatch.setattr(settings, "aws_bearer_token_bedrock", "bedrock-token")

    result = llm_config.seed_providers_from_env()

    assert result == {"created": 1, "updated": 0, "ordered": 0}
    row = _by_name("TestSeedBedrock")
    assert row is not None
    assert row["base_url"] == "sa-east-1"


def test_running_seed_twice_is_idempotent(seed_env, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider_priority", "OPENAI")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-openai")

    first = llm_config.seed_providers_from_env()
    second = llm_config.seed_providers_from_env()

    assert first == {"created": 1, "updated": 0, "ordered": 0}
    assert second == {"created": 0, "updated": 0, "ordered": 0}
    matches = [p for p in llm_config.list_providers() if p["name"] == "TestSeedOpenAI"]
    assert len(matches) == 1


def test_env_wins_key_and_order_ui_wins_model_and_base_url(seed_env, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider_priority", "OPENAI")
    monkeypatch.setattr(settings, "openai_api_key", "sk-original")
    llm_config.seed_providers_from_env()
    row = _by_name("TestSeedOpenAI")

    llm_config.update_provider(
        row["id"], model="gpt-4.1-mini", base_url="https://proxy.example/v1",
        order=999, enabled=False,
    )

    monkeypatch.setattr(settings, "openai_api_key", "sk-rotated")
    result = llm_config.seed_providers_from_env()

    assert result == {"created": 0, "updated": 1, "ordered": 1}
    after = _by_name("TestSeedOpenAI")
    assert _current_key(after["id"]) == "sk-rotated"
    assert after["model"] == "gpt-4.1-mini"
    assert after["base_url"] == "https://proxy.example/v1"
    assert after["order"] == 0
    assert after["enabled"] is True


def test_key_unchanged_does_not_trigger_an_update(seed_env, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider_priority", "OPENAI")
    monkeypatch.setattr(settings, "openai_api_key", "sk-stable")
    llm_config.seed_providers_from_env()

    result = llm_config.seed_providers_from_env()

    assert result == {"created": 0, "updated": 0, "ordered": 0}


def test_restore_then_seed_does_not_duplicate(seed_env, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "llm_provider_priority", "OPENAI")
    monkeypatch.setattr(settings, "vega_persist_dir", str(tmp_path))
    monkeypatch.setattr(settings, "openai_api_key", "sk-before-fresh-state")
    llm_config.seed_providers_from_env()

    exported = llm_config.export_providers_backup()
    assert exported >= 1

    row = _by_name("TestSeedOpenAI")
    llm_config.delete_provider(row["id"])

    llm_config.restore_providers_backup()

    monkeypatch.setattr(settings, "openai_api_key", "sk-after-fresh-state")
    llm_config.seed_providers_from_env()

    matches = [p for p in llm_config.list_providers() if p["name"] == "TestSeedOpenAI"]
    assert len(matches) == 1
    assert _current_key(matches[0]["id"]) == "sk-after-fresh-state"
