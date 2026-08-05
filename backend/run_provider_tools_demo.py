"""Smoke: todo provider (incl. Ollama local) chega em bind_tools / with_structured_output.

Offline — só monta os runnables, sem chamar provider. Guarda contra reintroduzir skip por
provider: tool-calling é capacidade do modelo, resolvida em runtime pela cascata, não por
allowlist estática (Ollama 0.32 + llama3.2 fazem tool-calling; ver SETUP-HISTORICO F-REAL-ENV-1).
"""
from __future__ import annotations

import sys

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.langchain_tools import CONCIERGE_TOOLS
from app.llm_models import (
    VegaBedrockChatModel,
    VegaChatAnthropic,
    VegaStubChatModel,
    build_chat_model,
)


class _Decision(BaseModel):
    next_agent: str = Field(description="Next specialist.")


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        print(f"FAIL {label}: {detail}", file=sys.stderr)
        sys.exit(1)
    print(f"  [ok] {label}", file=sys.stderr)


def main() -> None:
    openai = ChatOpenAI(model="gpt-4o-mini", api_key="sk-test", base_url="https://api.openai.com/v1")
    openai._vega_provider = "OpenAI"  # type: ignore[attr-defined]
    openai._vega_family = "openai"  # type: ignore[attr-defined]

    groq = ChatOpenAI(model="llama-3.3-70b", api_key="gsk-test", base_url="https://api.groq.com/openai/v1")
    groq._vega_provider = "Groq"  # type: ignore[attr-defined]
    groq._vega_family = "openai"  # type: ignore[attr-defined]

    ollama = ChatOpenAI(model="llama3.2", api_key="ollama", base_url="http://127.0.0.1:11434/v1")
    ollama._vega_provider = "Ollama Local"  # type: ignore[attr-defined]
    ollama._vega_family = "openai"  # type: ignore[attr-defined]

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

    providers = (
        ("OpenAI", openai),
        ("Groq", groq),
        ("Ollama Local", ollama),
        ("Anthropic", anthropic),
        ("Bedrock", bedrock),
        ("stub", VegaStubChatModel()),
    )

    print("== every provider binds tools (no static skip) ==", file=sys.stderr)
    for name, model in providers:
        check(f"{name} bind_tools", model.bind_tools(CONCIERGE_TOOLS) is not None)

    print("== every provider binds structured output ==", file=sys.stderr)
    for name, model in providers:
        if isinstance(model, VegaStubChatModel):
            continue  # stub responde JSON determinístico, sem with_structured_output
        check(f"{name} with_structured_output", model.with_structured_output(_Decision) is not None)

    print("== build_chat_model kinds ==", file=sys.stderr)
    built = {
        "openai": build_chat_model({
            "kind": "openai", "model": "gpt-4o-mini", "api_key": "sk-test",
            "base_url": "https://api.openai.com/v1", "name": "OpenAI",
        }),
        "ollama": build_chat_model({
            "kind": "openai", "model": "llama3.2", "api_key": "ollama",
            "base_url": "http://127.0.0.1:11434/v1", "name": "Ollama Local",
        }),
        "anthropic": build_chat_model({
            "kind": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "sk-ant",
            "base_url": "https://api.anthropic.com", "name": "Anthropic",
        }),
        "bedrock": build_chat_model({
            "kind": "bedrock", "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
            "api_key": "bedrock-key", "base_url": "us-east-1", "name": "Bedrock",
        }),
    }
    for kind, model in built.items():
        check(f"build_chat_model {kind}", model is not None)
        check(f"build_chat_model {kind} binds tools", model.bind_tools(CONCIERGE_TOOLS) is not None)

    print("All provider tool-calling checks passed.", file=sys.stderr)


if __name__ == "__main__":
    main()
