"""Amazon Bedrock na cascata (F-BEDROCK-1) — ex `test_bedrock_smoke.py`.

O grosso é offline (monta adapter/model, presets, CRUD SQLite). O teste que fala com a AWS de
verdade é `-m live` e exige `BEDROCK_API_KEY`.
"""
from __future__ import annotations

import os

import pytest

# `test_provider` renomeado no import: com o nome original o pytest o coletaria como teste.
from app.llm.llm import build_adapter
from app.llm.llm_providers import list_type_presets
from app.llm.llm import test_provider as call_test_provider
from app.llm.llm_config import create_provider, delete_provider, init_db
from app.llm.llm_models import build_chat_model

CFG = {
    "kind": "bedrock",
    "name": "bedrock-smoke",
    "base_url": os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    "model": os.getenv("BEDROCK_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
    "api_key": "smoke-offline-key",
}


@pytest.mark.bedrock
def test_bedrock_config_builds_an_adapter_and_a_chat_model():
    assert build_adapter(dict(CFG)) is not None
    assert build_chat_model(dict(CFG)) is not None


@pytest.mark.bedrock
@pytest.mark.parametrize("family", ["haiku", "sonnet", "opus"])
def test_bedrock_preset_offers_the_three_model_families(family):
    preset = next(p for p in list_type_presets() if p["type"] == "bedrock")
    assert any(family in m.lower() for m in preset["models"]), preset["models"]


@pytest.mark.bedrock
def test_bedrock_provider_survives_a_sqlite_round_trip():
    init_db()
    created = create_provider(
        CFG["name"], CFG["kind"], CFG["base_url"], CFG["model"], CFG["api_key"], enabled=True,
    )
    try:
        assert created["kind"] == "bedrock"
    finally:
        delete_provider(created["id"])


@pytest.mark.live
@pytest.mark.bedrock
def test_bedrock_live_call():
    api_key = os.getenv("BEDROCK_API_KEY", "")
    if not api_key:
        pytest.skip("BEDROCK_API_KEY não definida")
    result = call_test_provider({**CFG, "api_key": api_key})
    assert result.get("ok"), result
