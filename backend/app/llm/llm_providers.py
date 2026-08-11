"""Single base for LLM cascade (F-BACKEND-1).

`llm.py` (direct HTTP adapters) and `llm_models.py` (LangChain models) resolve the SAME cascade
via different paths, and so maintained copies of the same rule: which providers are active,
how to pin agent to connection, how to override model, where Bedrock region comes from.
Copies diverged without anyone noticing. Here that rule exists once; both modules become consumers.

What does NOT live here: how to talk to each provider. `llm.py` still owns HTTP adapters and
`llm_models.py` owns `BaseChatModel` — this module only decides WHICH config to use, in what order.
"""
from __future__ import annotations

import contextvars

from ..hub import agent_config
from ..settings import settings

# Cascade resolved ONCE per pipeline execution (`agents.run_workflow` etc). While
# set, nodes of that run use this frozen list instead of re-querying config source per call
# — so a `PUT` mid-run doesn't switch provider mid-flight.
current_provider_cfgs: contextvars.ContextVar = contextvars.ContextVar(
    "current_provider_cfgs", default=None,
)


# --- Connection type presets (F-021, stage 2) ---------
# Catalog of "Types" for connection UI: choosing a Type makes screen PREFILL
# `kind` + `base_url` + list of **budget models** suggested (editable dropdown).
# They're convenient DEFAULTS and **editable** — NOT authoritative: pricing/models change, so
# owner tweaks freely. `custom` leaves all free. All OpenAI-compatible except Claude
# (kind anthropic → AnthropicAdapter). Models = cheapest tier of each provider.
TYPE_PRESETS: list[dict] = [
    {"type": "openai", "label": "OpenAI", "kind": "openai",
     "base_url": "https://api.openai.com/v1",
     "models": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1-nano", "o4-mini", "gpt-3.5-turbo"]},
    {"type": "claude", "label": "Claude (Anthropic)", "kind": "anthropic",
     "base_url": "https://api.anthropic.com",
     "models": ["claude-haiku-4-5", "claude-3-5-haiku-latest", "claude-3-haiku-20240307",
                "claude-3-5-sonnet-latest"]},
    {"type": "grok", "label": "Grok (xAI)", "kind": "openai",
     "base_url": "https://api.x.ai/v1",
     "models": ["grok-3-mini", "grok-2-1212", "grok-beta"]},
    {"type": "groq", "label": "Groq", "kind": "openai",
     "base_url": "https://api.groq.com/openai/v1",
     "models": ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "gemma2-9b-it",
                "mixtral-8x7b-32768"]},
    {"type": "openrouter", "label": "OpenRouter", "kind": "openai",
     "base_url": "https://openrouter.ai/api/v1",
     "models": ["openai/gpt-4o-mini", "anthropic/claude-3.5-haiku",
                "meta-llama/llama-3.1-8b-instruct", "google/gemini-flash-1.5"]},
    {"type": "bedrock", "label": "Amazon Bedrock", "kind": "bedrock",
     "base_url": "us-east-1",
     "models": ["us.anthropic.claude-haiku-4-5-20251001-v1:0",
                "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "us.anthropic.claude-opus-4-5-20251101-v1:0",
                "anthropic.claude-3-haiku-20240307-v1:0",
                "anthropic.claude-3-5-sonnet-20241022-v2:0",
                "anthropic.claude-3-opus-20240229-v1:0"]},
    {"type": "custom", "label": "Custom", "kind": "openai", "base_url": "", "models": []},
]


def list_type_presets() -> list[dict]:
    """Type presets for connection UI (defensive copy — convenient/editable)."""
    return [dict(p) for p in TYPE_PRESETS]


def bedrock_region(base_url: str) -> str:
    """AWS region from `base_url` (field repurposed for bedrock) or environment."""
    return (base_url or settings.aws_default_region).strip() or "us-east-1"


# --- Cascade resolution -------------------------------------------------

def load_provider_configs() -> list[dict]:
    """ENABLED providers, in order, from ACTIVE config SOURCE.

    Today it's `LocalConfigSource` (SQLite via `llm_config`); in hub mode source is remote, without
    consumers changing. Missing table / standalone → empty list → stub only. Initial provider:
    `seed_ollama_default` at boot (Ollama Local)."""
    from ..hub import config_source  # import tardio: ciclo llm_providers↔config_source

    return config_source.get_active_source().get_llm_config()


def filter_provider_configs(cfgs: list[dict], connection: str = "", model: str = "") -> list[dict]:
    """Apply to ONE provider list the two override rules for config per agent (F-021):
    `connection` pins a provider (by id or name) and `model` overrides model of all
    remaining. It's this rule that was written twice."""
    if connection:
        cfgs = [c for c in cfgs if c.get("id") == connection or c.get("name") == connection]
    if model:
        cfgs = [{**c, "model": model} for c in cfgs]
    return cfgs


def resolve_provider_configs(connection: str = "", model: str = "") -> list[dict]:
    """Current cascade already filtered by overrides. Without `connection` or `model` is full
    cascade."""
    return filter_provider_configs(load_provider_configs(), connection, model)


def provider_configs_for_agent(agent_name: str = "") -> tuple[list[dict], str]:
    """Provider configs + stub model for an agent.

    Precedence order: agent's own config (connection/model) > frozen cascade of current run
    > cascade of active config."""
    stub_model = settings.llm_stub_model
    if agent_name:
        cfg = agent_config.get_agent(agent_name)
        stub_model = cfg.get("model") or settings.llm_stub_model
        if cfg.get("connection") or cfg.get("model"):
            cfgs = filter_provider_configs(
                load_provider_configs(), cfg.get("connection", ""), cfg.get("model", ""),
            )
            return cfgs, stub_model
    frozen = current_provider_cfgs.get()
    if frozen is not None:
        return frozen, stub_model
    return load_provider_configs(), stub_model
