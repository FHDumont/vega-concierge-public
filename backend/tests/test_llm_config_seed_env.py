"""`seed_providers_from_env()` — bootstrap da cascata por tokens do SO (F-BACKEND-3, Etapa B).

Nomes de teste usam specs isoladas (`TestSeedOpenAI`/`TestSeedClaude`/`TestSeedBedrock`) em vez
dos nomes reais ("OpenAI"/"Claude"/"Bedrock") — mesmo com o DB de teste isolado do `vega.db`
real (DT-036), manter nomes fictícios evita qualquer colisão com providers de produção se um
teste rodar contra outro DB por engano."""
from __future__ import annotations

import pytest

from app.llm import llm_config
from app.settings import settings

TEST_SPECS = (
    {"env_field": "openai_api_key", "name": "TestSeedOpenAI", "kind": "openai",
     "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    {"env_field": "anthropic_api_key", "name": "TestSeedClaude", "kind": "anthropic",
     "base_url": "https://api.anthropic.com", "model": "claude-sonnet-4-5"},
    {"env_field": "aws_bearer_token_bedrock", "name": "TestSeedBedrock", "kind": "bedrock",
     "base_url": "", "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0"},
)
TEST_NAMES = [s["name"] for s in TEST_SPECS]


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
    """Specs de teste + tokens zerados por padrão; cada teste liga o que precisa."""
    monkeypatch.setattr(llm_config, "_ENV_SEED_SPECS", TEST_SPECS)
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "aws_bearer_token_bedrock", "")
    monkeypatch.setattr(settings, "aws_default_region", "sa-east-1")
    yield
    for name in TEST_NAMES:
        row = _by_name(name)
        if row:
            llm_config.delete_provider(row["id"])


def test_no_token_is_a_noop(seed_env):
    result = llm_config.seed_providers_from_env()
    assert result == {"created": 0, "updated": 0}
    assert all(_by_name(n) is None for n in TEST_NAMES)


def test_creates_one_provider_per_token_present(seed_env, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-openai")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test-claude")

    result = llm_config.seed_providers_from_env()

    assert result == {"created": 2, "updated": 0}
    openai_row = _by_name("TestSeedOpenAI")
    claude_row = _by_name("TestSeedClaude")
    assert openai_row is not None and claude_row is not None
    assert openai_row["kind"] == "openai"
    assert openai_row["model"] == "gpt-4o-mini"
    assert openai_row["enabled"] is True
    assert claude_row["kind"] == "anthropic"
    assert claude_row["model"] == "claude-sonnet-4-5"
    # ordem: cloud empilha em MAX(ord)+10, um spec por vez
    assert claude_row["order"] == openai_row["order"] + 10
    assert _current_key(openai_row["id"]) == "sk-test-openai"
    assert _current_key(claude_row["id"]) == "sk-test-claude"
    assert "TestSeedBedrock" not in {openai_row["name"], claude_row["name"]}


def test_bedrock_resolves_base_url_from_aws_default_region(seed_env, monkeypatch):
    monkeypatch.setattr(settings, "aws_bearer_token_bedrock", "bedrock-token")

    result = llm_config.seed_providers_from_env()

    assert result == {"created": 1, "updated": 0}
    row = _by_name("TestSeedBedrock")
    assert row is not None
    assert row["base_url"] == "sa-east-1"


def test_running_seed_twice_is_idempotent(seed_env, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-openai")

    first = llm_config.seed_providers_from_env()
    second = llm_config.seed_providers_from_env()

    assert first == {"created": 1, "updated": 0}
    assert second == {"created": 0, "updated": 0}
    matches = [p for p in llm_config.list_providers() if p["name"] == "TestSeedOpenAI"]
    assert len(matches) == 1


def test_env_wins_the_key_ui_wins_the_rest(seed_env, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-original")
    llm_config.seed_providers_from_env()
    row = _by_name("TestSeedOpenAI")

    # Instrutor edita no Admin: outro modelo, outra ordem, desabilitado.
    llm_config.update_provider(
        row["id"], model="gpt-4.1-mini", base_url="https://proxy.example/v1",
        order=999, enabled=False,
    )

    # Token rotacionado no `.env` da VM.
    monkeypatch.setattr(settings, "openai_api_key", "sk-rotated")
    result = llm_config.seed_providers_from_env()

    assert result == {"created": 0, "updated": 1}
    after = _by_name("TestSeedOpenAI")
    assert _current_key(after["id"]) == "sk-rotated"
    # tudo que o Admin editou continua intocado
    assert after["model"] == "gpt-4.1-mini"
    assert after["base_url"] == "https://proxy.example/v1"
    assert after["order"] == 999
    assert after["enabled"] is False


def test_key_unchanged_does_not_trigger_an_update(seed_env, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-stable")
    llm_config.seed_providers_from_env()

    result = llm_config.seed_providers_from_env()

    assert result == {"created": 0, "updated": 0}


def test_restore_then_seed_does_not_duplicate(seed_env, monkeypatch, tmp_path):
    """Sequência real do boot pós fresh-state: `restore_providers_backup()` (que só restaura se
    a tabela SQLite estiver TOTALMENTE vazia — não controlável aqui, a suíte compartilha o
    `vega.db` do ambiente de dev) seguido de `seed_providers_from_env()`. Vale nos dois casos
    (restaurou ou não): no fim há UMA linha só, com a chave do `.env` mais recente — nunca
    duplicata."""
    monkeypatch.setattr(settings, "vega_persist_dir", str(tmp_path))
    monkeypatch.setattr(settings, "openai_api_key", "sk-before-fresh-state")
    llm_config.seed_providers_from_env()

    exported = llm_config.export_providers_backup()
    assert exported >= 1

    row = _by_name("TestSeedOpenAI")
    llm_config.delete_provider(row["id"])  # simula o fresh-state zerando o SQLite

    llm_config.restore_providers_backup()  # no-op se a tabela compartilhada não ficou vazia

    # token rotacionado depois do backup — o boot seguinte tem de propagar, não duplicar
    monkeypatch.setattr(settings, "openai_api_key", "sk-after-fresh-state")
    llm_config.seed_providers_from_env()

    matches = [p for p in llm_config.list_providers() if p["name"] == "TestSeedOpenAI"]
    assert len(matches) == 1
    assert _current_key(matches[0]["id"]) == "sk-after-fresh-state"
