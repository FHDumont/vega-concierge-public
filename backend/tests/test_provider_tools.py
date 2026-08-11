"""Provider parity in the cascade (ADR-034) — formerly `run_provider_tools_demo.py`.

Offline: only assembles the runnables, no provider is called (not even the Ollama at `base_url`).
Guards against reintroducing a per-provider skip — tool-calling is a model capability, resolved
at runtime, not a static allowlist.
"""
from __future__ import annotations

import pytest
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.store.langchain_tools import CONCIERGE_TOOLS
from app.llm.llm_models import (
    VegaBedrockChatModel,
    VegaChatAnthropic,
    build_chat_model,
)
from app.llm.stub import VegaStubChatModel


class _Decision(BaseModel):
    next_agent: str = Field(description="Next specialist.")


def _openai_like(name: str, model: str, api_key: str, base_url: str):
    chat = ChatOpenAI(model=model, api_key=api_key, base_url=base_url)
    chat._vega_provider = name  # type: ignore[attr-defined]
    chat._vega_family = "openai"  # type: ignore[attr-defined]
    return chat


def _providers() -> dict[str, object]:
    anthropic = VegaChatAnthropic(model="claude-sonnet-4-20250514", api_key="sk-ant-test")
    anthropic._vega_provider = "Anthropic"  # type: ignore[attr-defined]
    anthropic._vega_family = "anthropic"  # type: ignore[attr-defined]

    bedrock = VegaBedrockChatModel(
        model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        anthropic_api_key="unused",
        bedrock_api_key="bedrock-test",
        region="us-east-1",
    )
    bedrock._vega_provider = "Bedrock"  # type: ignore[attr-defined]
    bedrock._vega_family = "bedrock"  # type: ignore[attr-defined]

    return {
        "OpenAI": _openai_like("OpenAI", "gpt-4o-mini", "sk-test", "https://api.openai.com/v1"),
        "Groq": _openai_like("Groq", "llama-3.3-70b", "gsk-test", "https://api.groq.com/openai/v1"),
        "Ollama Local": _openai_like("Ollama Local", "llama3.2", "ollama", "http://127.0.0.1:11434/v1"),
        "Anthropic": anthropic,
        "Bedrock": bedrock,
        "stub": VegaStubChatModel(),
    }


PROVIDERS = _providers()


@pytest.mark.parametrize("name", sorted(PROVIDERS))
def test_every_provider_binds_tools(name):
    assert PROVIDERS[name].bind_tools(CONCIERGE_TOOLS) is not None


@pytest.mark.parametrize("name", sorted(n for n in PROVIDERS if n != "stub"))
def test_every_provider_binds_structured_output(name):
    # The stub responds with deterministic JSON, without `with_structured_output`.
    assert PROVIDERS[name].with_structured_output(_Decision) is not None


BUILD_CONFIGS = {
    "openai": {"kind": "openai", "model": "gpt-4o-mini", "api_key": "sk-test",
               "base_url": "https://api.openai.com/v1", "name": "OpenAI"},
    "ollama": {"kind": "openai", "model": "llama3.2", "api_key": "ollama",
               "base_url": "http://127.0.0.1:11434/v1", "name": "Ollama Local"},
    "anthropic": {"kind": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "sk-ant",
                  "base_url": "https://api.anthropic.com", "name": "Anthropic"},
    "bedrock": {"kind": "bedrock", "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
                "api_key": "bedrock-key", "base_url": "us-east-1", "name": "Bedrock"},
}


@pytest.mark.parametrize("kind", sorted(BUILD_CONFIGS))
def test_build_chat_model_produces_a_tool_capable_model(kind):
    model = build_chat_model(BUILD_CONFIGS[kind])
    assert model is not None
    assert model.bind_tools(CONCIERGE_TOOLS) is not None
