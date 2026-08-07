"""Base única da cascata (F-BACKEND-1).

Antes desta fase a regra de "quais providers, em que ordem, com que override" existia duas
vezes — em `llm.get_llm_for` (adapters HTTP) e em `llm_models._provider_cfgs_for_agent`
(modelos LangChain). Estes testes fixam que agora é uma só.
"""
from __future__ import annotations

import pytest

from app.llm import llm, llm_models, llm_providers

CFGS = [
    {"id": "LP-1", "name": "primeiro", "kind": "openai", "base_url": "https://a/v1",
     "model": "m-1", "api_key": "k1"},
    {"id": "LP-2", "name": "segundo", "kind": "openai", "base_url": "https://b/v1",
     "model": "m-2", "api_key": "k2"},
]


@pytest.fixture
def fixed_cascade(monkeypatch):
    monkeypatch.setattr(llm_providers, "load_provider_configs", lambda: [dict(c) for c in CFGS])
    return CFGS


def test_no_override_keeps_the_whole_cascade_in_order(fixed_cascade):
    assert [c["id"] for c in llm_providers.resolve_provider_configs()] == ["LP-1", "LP-2"]


@pytest.mark.parametrize("connection", ["LP-2", "segundo"])
def test_connection_pins_a_single_provider_by_id_or_name(fixed_cascade, connection):
    resolved = llm_providers.resolve_provider_configs(connection=connection)
    assert [c["id"] for c in resolved] == ["LP-2"]


def test_model_override_applies_to_every_remaining_provider(fixed_cascade):
    resolved = llm_providers.resolve_provider_configs(model="m-override")
    assert [c["model"] for c in resolved] == ["m-override", "m-override"]


def test_unknown_connection_resolves_to_nothing(fixed_cascade):
    # Fixar num provider desabilitado/ausente tem de esvaziar a cascata — quem chama acrescenta
    # o stub e a app segue offline em vez de estourar.
    assert llm_providers.resolve_provider_configs(connection="LP-inexistente") == []


def test_both_paths_resolve_the_same_provider_order(fixed_cascade):
    """O ponto da unificação: adapters HTTP e modelos LangChain veem a MESMA cascata.

    Fora de um run do pipeline a cascata congelada não está setada, então os dois caminhos
    partem da mesma leitura da fonte de config.
    """
    assert llm_providers.current_provider_cfgs.get() is None

    adapters = llm.get_llm_for().adapters
    http_models = [getattr(a, "model", None) for a in adapters]

    langchain_cfgs, _ = llm_providers.provider_configs_for_agent()
    langchain_models = [c["model"] for c in langchain_cfgs]

    # O caminho HTTP acrescenta o StubLLM no fim; tirando ele, a ordem é idêntica.
    assert http_models[:-1] == langchain_models == ["m-1", "m-2"]


def test_agent_override_wins_over_the_frozen_run_cascade(fixed_cascade, monkeypatch):
    monkeypatch.setattr(
        llm_providers.agent_config, "get_agent",
        lambda name: {"connection": "LP-2", "model": "m-do-agente"},
    )
    token = llm_providers.current_provider_cfgs.set([{"id": "LP-congelado", "model": "m-frozen"}])
    try:
        cfgs, stub_model = llm_providers.provider_configs_for_agent("curator")
    finally:
        llm_providers.current_provider_cfgs.reset(token)

    assert [c["id"] for c in cfgs] == ["LP-2"]
    assert [c["model"] for c in cfgs] == ["m-do-agente"]
    assert stub_model == "m-do-agente"


def test_frozen_run_cascade_wins_when_the_agent_has_no_override(fixed_cascade, monkeypatch):
    monkeypatch.setattr(
        llm_providers.agent_config, "get_agent", lambda name: {"connection": "", "model": ""},
    )
    token = llm_providers.current_provider_cfgs.set([{"id": "LP-congelado", "model": "m-frozen"}])
    try:
        cfgs, _ = llm_providers.provider_configs_for_agent("curator")
    finally:
        llm_providers.current_provider_cfgs.reset(token)

    assert [c["id"] for c in cfgs] == ["LP-congelado"]


@pytest.mark.parametrize("base_url,expected", [
    ("sa-east-1", "sa-east-1"),
    ("  us-west-2  ", "us-west-2"),
    ("", "us-east-1"),
])
def test_bedrock_region_derivation_is_shared(base_url, expected, monkeypatch):
    monkeypatch.setattr(llm_providers.settings, "aws_default_region", "us-east-1")
    assert llm_providers.bedrock_region(base_url) == expected


def test_type_presets_are_a_defensive_copy():
    first = llm_providers.list_type_presets()
    first[0]["label"] = "mexido"
    assert llm_providers.list_type_presets()[0]["label"] != "mexido"


def test_admin_test_provider_button_works_offline(api_client):
    """O botão "test provider" do Admin: cria, testa e apaga, sem rede."""
    from app.store import users

    users.seed_owner_user()
    owner = users.get_user_by_email(users.OWNER_EMAIL)
    headers = {"Authorization": f"Bearer {users.create_session(owner['id'])}"}

    created = api_client.post(
        "/api/admin/config/providers", headers=headers,
        json={"name": "botao-test", "kind": "openai", "base_url": "http://127.0.0.1:1/v1",
              "model": "gpt-4o-mini", "api_key": "sk-offline", "enabled": False},
    ).json()
    try:
        result = api_client.post(
            f"/api/admin/config/providers/{created['id']}/test", headers=headers, json={},
        ).json()
        # Sem rede o teste FALHA — o que se garante é o contrato da resposta e que a chave
        # nunca volta ao front.
        assert set(result) >= {"ok"}
        assert "sk-offline" not in str(result)
    finally:
        api_client.delete(f"/api/admin/config/providers/{created['id']}", headers=headers)


def test_llm_models_no_longer_carries_its_own_cascade_rule():
    assert not hasattr(llm_models, "_provider_cfgs_for_agent")
    assert not hasattr(llm, "_load_provider_configs")
