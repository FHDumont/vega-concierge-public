"""Cliente LLM multi-provider com fallback em cascata (F-020).

Adapters reais (OpenAI-compatível e Anthropic) + StubLLM offline como último recurso.
A cascata tenta os providers habilitados na ordem; em falha (erro/timeout/rate-limit)
cai para o próximo; o StubLLM garante que a app continua standalone sem nenhuma chave.

O provider é resolvido POR CHAMADA a partir da config corrente (`_load_provider_configs`),
não fixado no import — mudar a config aplica sem restart. A FONTE da config evolui sem
que os consumidores (`get_llm`) mudem: env (etapa 1) → SQLite (etapa 2) → ConfigSource
local/remota (etapa 4 / F-021). Só `_load_provider_configs` troca de implementação.

HTTP via SDKs oficiais (`openai`, `anthropic`) — F-OBS-PREP-1 / ADR-027; readiness
Splunk Agent Observability/OTel. Chaves de API são SEGREDOS: nunca são logadas nem retornadas.
"""
import os
import random
import time
from dataclasses import dataclass

from anthropic import Anthropic, AnthropicBedrock
from openai import OpenAI

from .http_ssl import sync_http_client

# Timeout por chamada ao provider (s). Curto p/ a cascata cair rápido no fallback.
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "20"))
DEFAULT_STUB_MODEL = os.getenv("LLM_STUB_MODEL", "gpt-4o-mini")


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str = "stub"   # nome amigável do provider que respondeu
    system: str = "stub"     # família do provider (openai | anthropic | stub)
    fallback: bool = False   # True se um provider anterior na cascata falhou antes deste
    prompt_cache_tokens: int = 0  # tokens de input cobertos por prompt-cache do provider (F-COST-CACHE)


# --- Adapters ---------------------------------------------------------------

class StubLLM:
    """Determinístico-ish, gera tokens plausíveis sem chamar rede. Último recurso
    da cascata → mantém a app funcionando offline sem nenhuma chave configurada."""
    name = "stub"
    system = "stub"

    def __init__(self, model: str = DEFAULT_STUB_MODEL):
        self.model = model

    def complete(self, system: str, prompt: str, verbose: bool = False, max_tokens: int | None = None) -> LLMResult:
        time.sleep(0.05)
        out = f"[stub:{self.model}] resposta para: {prompt[:48]}"
        in_tok = max(8, len(system.split()) + len(prompt.split()))
        # `max_tokens` (teto por feature — F-022) limita o tamanho sintético; senão usa o default.
        out_tok = (max_tokens or (120 if verbose else 30)) + random.randint(0, 10)
        return LLMResult(out, in_tok, out_tok, self.model, provider=self.name, system=self.system)


class OpenAICompatAdapter:
    """Chat Completions OpenAI-compatível: cobre OpenAI, Groq, xAI/Grok, OpenRouter e
    afins (basta `base_url` + `api_key` + `model`). Tokens/modelo reais vêm da resposta."""
    system = "openai"

    def __init__(self, name: str, base_url: str, api_key: str, model: str):
        self.name = name
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            timeout=LLM_TIMEOUT_S,
            http_client=sync_http_client(LLM_TIMEOUT_S),
        )

    def complete(self, system: str, prompt: str, verbose: bool = False, max_tokens: int | None = None) -> LLMResult:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens or (512 if verbose else 256),
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        cache_tok = 0
        if usage is not None:
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                cache_tok = int(getattr(details, "cached_tokens", 0) or 0)
        return LLMResult(
            text,
            int(usage.prompt_tokens if usage else 0),
            int(usage.completion_tokens if usage else 0),
            response.model or self.model,
            provider=self.name,
            system=self.system,
            prompt_cache_tokens=cache_tok,
        )


class AnthropicAdapter:
    """Anthropic Messages API (Claude). `base_url` default oficial; tokens reais da resposta.
    Alternativa (decisão em aberto da spec): rotear Claude via OpenRouter usando o adapter
    OpenAI-compatível — suportamos os DOIS caminhos (ADR-015)."""
    system = "anthropic"
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, name: str, base_url: str, api_key: str, model: str):
        self.name = name
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client = Anthropic(
            api_key=api_key,
            base_url=self.base_url,
            timeout=LLM_TIMEOUT_S,
            default_headers={"anthropic-version": self.ANTHROPIC_VERSION},
            http_client=sync_http_client(LLM_TIMEOUT_S),
        )

    def complete(self, system: str, prompt: str, verbose: bool = False, max_tokens: int | None = None) -> LLMResult:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens or (512 if verbose else 256),
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        usage = response.usage
        cache_tok = 0
        if usage is not None:
            cache_tok = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            if not cache_tok:
                details = getattr(usage, "input_token_details", None) or getattr(usage, "cache_creation", None)
                if details is not None and hasattr(details, "cache_read"):
                    cache_tok = int(getattr(details, "cache_read", 0) or 0)
                elif isinstance(details, dict):
                    cache_tok = int(details.get("cache_read") or details.get("cache_read_input_tokens") or 0)
        return LLMResult(
            text,
            int(usage.input_tokens if usage else 0),
            int(usage.output_tokens if usage else 0),
            response.model or self.model,
            provider=self.name,
            system=self.system,
            prompt_cache_tokens=cache_tok,
        )


def _bedrock_region(base_url: str) -> str:
    """Região AWS a partir de `base_url` (campo reutilizado p/ bedrock) ou env."""
    region = (base_url or os.getenv("AWS_DEFAULT_REGION", "us-east-1")).strip()
    return region or "us-east-1"


def make_bedrock_client(region: str, api_key: str, model: str, timeout: float = LLM_TIMEOUT_S):
    """Cliente Bedrock via API key (UI). Usa AnthropicBedrock (bedrock-runtime) — Mantle exige IAM."""
    if not api_key:
        raise ValueError("Bedrock provider requires api_key (Bedrock API key from Admin UI)")
    return AnthropicBedrock(
        aws_region=region,
        timeout=timeout,
        http_client=sync_http_client(timeout),
        api_key=api_key,
    )


class BedrockAdapter:
    """Amazon Bedrock (Claude via Anthropic SDK). `base_url` = região AWS; `api_key` = Bedrock API key
    cadastrada na UI (workshop não usa IAM/credential chain no runtime)."""
    system = "bedrock"

    def __init__(self, name: str, base_url: str, api_key: str, model: str):
        self.name = name
        self.region = _bedrock_region(base_url)
        self.api_key = api_key or ""
        self.model = model
        self._client = make_bedrock_client(self.region, self.api_key, self.model)

    def complete(self, system: str, prompt: str, verbose: bool = False, max_tokens: int | None = None) -> LLMResult:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens or (512 if verbose else 256),
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        usage = response.usage
        cache_tok = 0
        if usage is not None:
            cache_tok = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        return LLMResult(
            text,
            int(usage.input_tokens if usage else 0),
            int(usage.output_tokens if usage else 0),
            response.model or self.model,
            provider=self.name,
            system=self.system,
            prompt_cache_tokens=cache_tok,
        )


# kind da config → classe do adapter. Acrescentar outro provider = uma linha aqui.
_ADAPTERS = {"openai": OpenAICompatAdapter, "anthropic": AnthropicAdapter, "bedrock": BedrockAdapter}


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


def build_adapter(cfg: dict):
    """Constrói um adapter a partir de um dict de config. Retorna None se falta kind/modelo/api_key."""
    kind = cfg.get("kind", "openai")
    cls = _ADAPTERS.get(kind)
    if cls is None or not cfg.get("model") or not cfg.get("api_key"):
        return None
    return cls(
        cfg.get("name", cfg.get("kind", "?")),
        cfg.get("base_url", ""),
        cfg.get("api_key", ""),
        cfg["model"],
    )


# --- Cascata ----------------------------------------------------------------

class CascadeLLM:
    """Tenta os adapters em ordem; em falha (erro/timeout/rate-limit) cai p/ o próximo.
    O último adapter é sempre o StubLLM → a app nunca fica sem resposta (standalone-first)."""

    def __init__(self, adapters: list):
        self.adapters = adapters  # ordenados; StubLLM por último

    def primary_model(self) -> str:
        """Modelo do adapter de maior prioridade (1º da cascata) — usado como parte da CHAVE de
        cache (F-022) ANTES da chamada. Stub por último garante sempre um valor."""
        return self.adapters[0].model if self.adapters else DEFAULT_STUB_MODEL

    def complete(self, system: str, prompt: str, verbose: bool = False, max_tokens: int | None = None) -> LLMResult:
        last_err = None
        for i, adapter in enumerate(self.adapters):
            try:
                r = adapter.complete(system, prompt, verbose=verbose, max_tokens=max_tokens)
                r.fallback = i > 0  # algum provider anterior falhou antes deste responder
                return r
            except Exception as e:  # noqa: BLE001 — qualquer falha derruba p/ o próximo (não loga: a msg pode citar a chave)
                last_err = e
                continue
        # Inalcançável na prática (StubLLM não falha), mas seja explícito.
        raise RuntimeError(f"todos os providers da cascata falharam: {type(last_err).__name__}")


# --- Resolução da config (fonte evolui; consumidores não) -------------------

def _load_provider_configs() -> list[dict]:
    """Providers habilitados, em ordem — resolvidos pela FONTE de config ATIVA (etapa 4).
    Hoje é a `LocalConfigSource` (SQLite via `llm_config`); a F-021 troca p/ uma fonte remota
    via `config_source.set_active_source`, sem que este consumidor mude. Tabela ausente /
    standalone → lista vazia → só stub. Provider inicial: `seed_ollama_default` no boot (Ollama Local)."""
    from . import config_source  # import tardio: evita ciclo e mantém llm.py importável standalone
    return config_source.get_active_source().get_llm_config()


def get_llm() -> CascadeLLM:
    """Cascata resolvida a partir da config CORRENTE (por chamada, não no import) — mudar
    a config aplica sem restart. Sempre termina no StubLLM (último recurso offline)."""
    adapters = [a for a in (build_adapter(c) for c in _load_provider_configs()) if a is not None]
    adapters.append(StubLLM())
    return CascadeLLM(adapters)


def get_llm_for(connection: str = "", model: str = "") -> CascadeLLM:
    """LLM resolvido para UM agente (F-021). `connection` = id do provider a fixar (ou '' =
    cascata completa); `model` = override opcional do modelo. Sempre termina no StubLLM →
    fixar numa conexão desabilitada/ausente cai p/ o stub (offline). Sem connection nem model
    é equivalente a `get_llm()` (cascata corrente)."""
    cfgs = _load_provider_configs()
    if connection:  # fixa este agente num provider específico da cascata
        cfgs = [c for c in cfgs if c.get("id") == connection or c.get("name") == connection]
    if model:  # override do modelo (aplica ao(s) provider(s) resolvido(s))
        cfgs = [{**c, "model": model} for c in cfgs]
    adapters = [a for a in (build_adapter(c) for c in cfgs) if a is not None]
    adapters.append(StubLLM(model or DEFAULT_STUB_MODEL))
    return CascadeLLM(adapters)


def test_provider(cfg: dict) -> dict:
    """Chamada de teste a UM provider (botão 'Test' da config — F-020 etapa 3). Mede latência
    e usa modelo/tokens reais. NÃO loga nem ecoa a chave; em erro devolve tipo+mensagem (a
    exceção dos adapters não contém a chave, que vai só no header)."""
    adapter = build_adapter(cfg)
    if adapter is None:
        return {"ok": False, "error": "provider incompleto (kind/model/api_key)"}
    t0 = time.perf_counter()
    try:
        r = adapter.complete("You are a health check.", "Reply with the word OK.", verbose=False)
        return {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "provider": r.provider, "model": r.model,
                "input_tokens": r.input_tokens, "output_tokens": r.output_tokens}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": f"{type(e).__name__}: {e}"}


def test_agent(connection: str = "", model: str = "", system: str = "You are a health check.") -> dict:
    """Test por agente (F-021): resolve o LLM do agente (`get_llm_for`) e faz UMA chamada real
    com o system prompt efetivo. Como a cascata sempre termina no stub, `ok` é True mesmo offline
    — `provider`/`model` mostram o que o agente REALMENTE usaria (stub se nada configurado/caiu)."""
    llm = get_llm_for(connection, model)
    t0 = time.perf_counter()
    try:
        r = llm.complete(system, "Reply with the word OK.", verbose=False)
        return {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "provider": r.provider, "model": r.model,
                "input_tokens": r.input_tokens, "output_tokens": r.output_tokens}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": f"{type(e).__name__}: {e}"}
