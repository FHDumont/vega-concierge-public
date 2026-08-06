"""Base única da cascata de LLM (F-BACKEND-1).

`llm.py` (adapters HTTP diretos) e `llm_models.py` (modelos LangChain) resolvem a MESMA cascata
por caminhos diferentes, e por isso mantinham cópias da mesma regra: quais providers estão
ativos, como fixar um agente numa conexão, como sobrescrever o modelo, de onde sai a região do
Bedrock. As cópias divergiram sem ninguém notar. Aqui essa regra existe uma vez só; os dois
módulos viram consumidores.

O que NÃO mora aqui: como falar com cada provider. `llm.py` continua dono dos adapters HTTP e
`llm_models.py` dos `BaseChatModel` — este módulo só decide QUAL config usar, em que ordem.
"""
from __future__ import annotations

import contextvars

from . import agent_config
from .settings import settings

# Cascata resolvida UMA vez por execução do pipeline (`agents.run_workflow` e afins). Enquanto
# estiver setada, os nós daquele run usam essa lista congelada em vez de reconsultar a fonte de
# config a cada chamada — assim um `PUT` no meio do run não troca de provider na metade.
current_provider_cfgs: contextvars.ContextVar = contextvars.ContextVar(
    "current_provider_cfgs", default=None,
)


# --- Type presets de conexão (F-021, etapa 2) -------------------------------
# Catálogo de "Types" para a UI de conexão: ao escolher um Type, a tela faz PREFILL de
# `kind` + `base_url` + uma lista de **modelos econômicos** sugeridos (dropdown editável).
# São defaults CONVENIENTES e **editáveis** — NÃO autoritativos: pricing/modelos mudam, então
# o owner ajusta livremente. `custom` deixa tudo livre. Todos OpenAI-compatíveis exceto Claude
# (kind anthropic → AnthropicAdapter). Modelos = a classe mais barata de cada provider.
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
    """Presets de Type p/ a UI de conexão (cópia defensiva — convenientes/editáveis)."""
    return [dict(p) for p in TYPE_PRESETS]


def bedrock_region(base_url: str) -> str:
    """Região AWS a partir de `base_url` (campo reaproveitado p/ bedrock) ou do ambiente."""
    return (base_url or settings.aws_default_region).strip() or "us-east-1"


# --- Resolução da cascata ----------------------------------------------------

def load_provider_configs() -> list[dict]:
    """Providers habilitados, em ordem, pela FONTE de config ATIVA.

    Hoje é a `LocalConfigSource` (SQLite via `llm_config`); em modo hub a fonte é remota, sem
    que os consumidores mudem. Tabela ausente / standalone → lista vazia → só stub. Provider
    inicial: `seed_ollama_default` no boot (Ollama Local)."""
    from . import config_source  # import tardio: ciclo llm_providers↔config_source

    return config_source.get_active_source().get_llm_config()


def filter_provider_configs(cfgs: list[dict], connection: str = "", model: str = "") -> list[dict]:
    """Aplica a UMA lista de providers as duas regras de override da config por agente (F-021):
    `connection` fixa um provider (por id ou nome) e `model` sobrescreve o modelo de todos os
    que sobrarem. Era esta regra que estava escrita duas vezes."""
    if connection:
        cfgs = [c for c in cfgs if c.get("id") == connection or c.get("name") == connection]
    if model:
        cfgs = [{**c, "model": model} for c in cfgs]
    return cfgs


def resolve_provider_configs(connection: str = "", model: str = "") -> list[dict]:
    """Cascata corrente já filtrada pelos overrides. Sem `connection` nem `model` é a cascata
    inteira."""
    return filter_provider_configs(load_provider_configs(), connection, model)


def provider_configs_for_agent(agent_name: str = "") -> tuple[list[dict], str]:
    """Configs de provider + modelo do stub para um agente.

    Ordem de precedência: config do próprio agente (connection/model) > cascata congelada do run
    corrente > cascata da config ativa."""
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
