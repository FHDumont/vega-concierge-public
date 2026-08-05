"""Smoke F-BEDROCK-1 — build adapter/model offline; live test opcional com Bedrock API key."""
from __future__ import annotations

import os
import sys

from app.llm import build_adapter, list_type_presets, test_provider
from app.llm_config import create_provider, init_db, list_providers, delete_provider
from app.llm_models import build_chat_model

_SMOKE_CFG = {
    "kind": "bedrock",
    "name": "bedrock-smoke",
    "base_url": os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    "model": os.getenv("BEDROCK_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
    "api_key": "smoke-offline-key",
}


def main() -> int:
    cfg = dict(_SMOKE_CFG)
    adapter = build_adapter(cfg)
    model = build_chat_model(cfg)
    assert adapter is not None, "build_adapter returned None"
    assert model is not None, "build_chat_model returned None"
    presets = list_type_presets()
    bedrock = next(p for p in presets if p["type"] == "bedrock")
    assert bedrock, "bedrock preset missing"
    for needle in ("haiku", "sonnet", "opus"):
        assert any(needle in m.lower() for m in bedrock["models"]), f"{needle} model missing in preset"
    print("build_adapter:", type(adapter).__name__)
    print("build_chat_model:", type(model).__name__)
    print("presets ok")

    live_key = os.getenv("BEDROCK_API_KEY", "")
    if os.getenv("BEDROCK_LIVE_TEST") == "1":
        if not live_key:
            print("BEDROCK_LIVE_TEST=1 requires BEDROCK_API_KEY", file=sys.stderr)
            return 1
        live_cfg = {**cfg, "api_key": live_key}
        result = test_provider(live_cfg)
        print("live test:", result)
        if not result.get("ok"):
            return 1
    else:
        print("live test skipped (set BEDROCK_LIVE_TEST=1 + Bedrock API key to run)")

    init_db()
    created = create_provider(
        cfg["name"], cfg["kind"], cfg["base_url"], cfg["model"], live_key or cfg["api_key"], enabled=True
    )
    assert created["kind"] == "bedrock"
    delete_provider(created["id"])
    print("sqlite CRUD ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
