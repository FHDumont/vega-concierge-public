"""Camada de controle de custo de LLM (F-022) — transversal a TODA chamada de IA das features.

Para que "IA em toda a app" não exploda token, toda feature passa por aqui:
- **Cache de resposta** por chave `(feature, input normalizado, model, system_hash, max_tokens, verbose)`, com TTL configurável.
  In-memory (basta p/ a VM, 1 usuário; reseta no restart — decisão da spec, não SQLite).
- **single-flight**: chamadas idênticas concorrentes (ex.: simulador) dedupam — só UMA vai ao
  provider, as demais reusam o resultado. (locks por chave).
- **rate-limit por instância**: janela deslizante; ao estourar, degrada p/ o StubLLM (offline,
  sem gasto) em vez de quebrar — standalone-first. Marca o motivo no status.
- **max_tokens** por feature: o teto vai direto ao adapter (ver `llm.py`).

Não resolve config — quem chama (`agents.feature_complete`) usa o resultado (hit|miss). Sem
dependência nova; thread-safe (endpoints sync rodam em threadpool).
"""
import hashlib
import threading
import time
from typing import Literal

from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..galileo_span import (
    BUSINESS_STEPS,
    RESPONSE_CACHE_TOOL_NAME,
    llm_run_name,
    response_cache_invoke_run_name,
    response_cache_replay_run_name,
)
from .llm import LLMResult, StubLLM
from ..runnable_config import current_runnable_config
from ..settings import settings

# Knobs por env (defaults convenientes p/ o workshop; rate generoso p/ não atrapalhar uso normal,
# mas conter floods do simulador / cost_spike). TTL do cache em segundos.
CACHE_TTL_S = settings.llm_cache_ttl_s
CACHE_MAX = settings.llm_cache_max
RATE_MAX = settings.llm_rate_max              # nº de chamadas reais ao provider...
RATE_WINDOW_S = settings.llm_rate_window_s    # ...por janela (s); <=0 desliga.


def cache_globally_enabled() -> bool:
    """True quando `LLM_CACHE_ENABLED` está ligado (default `1`). Falsy: 0/false/no/off."""
    return settings.llm_cache_enabled


# UC-5 (F-WORKSHOP-STAB-4): cache hit não invoca o modelo → sem `model.invoke` não nasce span
# `[llm]`, e sem o span os evaluators de PII/tone não têm o que avaliar. Choke point declarativo
# em vez de espalhar `use_cache=False` por call site — espelha `galileo_control.CONTROL_FEATURES_POST`
# (`obs/galileo_control.py:25`) sem inverter a camada (`llm/` não importa `obs/`). Escopo restrito
# aos dois alvos de post/Steer por ora: `product_qa`/`search` (pre) são as features mais chamadas
# da loja e a chave de cache já inclui a pergunta — incluir só se um hit real aparecer na UC-1.
NO_RESPONSE_CACHE_FEATURES: frozenset[str] = frozenset({"notification_copy"})


def cache_enabled_for(feature: str, use_cache: bool) -> bool:
    return use_cache and cache_globally_enabled() and feature not in NO_RESPONSE_CACHE_FEATURES


def normalize(text: str) -> str:
    """Normaliza o input p/ a chave de cache: colapsa espaços + lowercase (hits robustos a
    variações triviais de digitação/whitespace)."""
    return " ".join((text or "").split()).lower()


def system_hash(system: str) -> str:
    """Hash curto do system prompt (F-COST-CACHE): muda o system → miss limpo, sem stale."""
    return hashlib.sha256(normalize(system).encode("utf-8")).hexdigest()[:16]


def make_cache_key(feature: str, prompt: str, model_key: str, *,
                   system: str = "", max_tokens: int | None = None, verbose: bool = False):
    """Chave estável: feature + prompt norm + model + system_hash + max_tokens + verbose."""
    return (
        feature,
        normalize(prompt),
        model_key,
        system_hash(system),
        int(max_tokens) if max_tokens is not None else 0,
        bool(verbose),
    )


class ResponseCache:
    """Cache TTL simples (dict + lock). Chave arbitrária → (expira_em, valor). Evicção: ao
    encher, descarta entradas expiradas e, se ainda cheio, a mais antiga (FIFO aproximado)."""

    def __init__(self, ttl: float = CACHE_TTL_S, maxsize: int = CACHE_MAX):
        self.ttl = ttl
        self.maxsize = maxsize
        self._d: dict = {}  # key -> (expires_monotonic, value)
        self._lock = threading.Lock()

    def get(self, key):
        now = time.monotonic()
        with self._lock:
            entry = self._d.get(key)
            if entry is None:
                return None
            expires, value = entry
            if expires < now:
                self._d.pop(key, None)
                return None
            return value

    def put(self, key, value) -> None:
        with self._lock:
            if len(self._d) >= self.maxsize and key not in self._d:
                self._evict_locked()
            self._d[key] = (time.monotonic() + self.ttl, value)

    def _evict_locked(self) -> None:
        now = time.monotonic()
        expired = [k for k, (exp, _) in self._d.items() if exp < now]
        for k in expired:
            self._d.pop(k, None)
        if len(self._d) >= self.maxsize:  # ainda cheio → descarta o mais antigo inserido
            oldest = next(iter(self._d), None)
            if oldest is not None:
                self._d.pop(oldest, None)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()

    def __len__(self) -> int:
        return len(self._d)


class _KeyedLocks:
    """Mapa de locks por chave p/ o single-flight: serializa chamadas idênticas concorrentes."""

    def __init__(self):
        self._locks: dict = {}
        self._guard = threading.Lock()

    def get(self, key) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
            return lock


class RateLimiter:
    """Rate-limit por instância (janela deslizante). `allow()` True se ainda há orçamento na
    janela. `maxn<=0` desliga (sempre permite)."""

    def __init__(self, maxn: int = RATE_MAX, window: float = RATE_WINDOW_S):
        self.maxn = maxn
        self.window = window
        self._hits: list = []
        self._lock = threading.Lock()

    def allow(self) -> bool:
        if self.maxn <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            self._hits = [t for t in self._hits if now - t < self.window]
            if len(self._hits) >= self.maxn:
                return False
            self._hits.append(now)
            return True


# Singletons por instância (1 backend por VM). Trocáveis em teste via reset_state().
_cache = ResponseCache()
_inflight = _KeyedLocks()
_limiter = RateLimiter()


class _CheckResponseCacheInput(BaseModel):
    """Prompt da feature usado como chave do cache F-022."""

    input: str = Field(description="Feature prompt used as the F-022 response-cache key.")


def _feature_run_name(feature: str) -> str:
    step = BUSINESS_STEPS.get(feature, feature.replace("-", "_"))
    return llm_run_name("feature", step)


def _make_check_response_cache_tool(
    feature: str,
    *,
    cache: Literal["hit", "miss"],
    cached: LLMResult | None = None,
) -> StructuredTool:
    """StructuredTool compartilhado — decisão F-022 visível como tool span no Console."""

    def check_response_cache(input: str) -> dict:
        out: dict = {"cache": cache, "feature": feature, "input": input}
        if cache == "hit" and cached is not None:
            out.update({
                "model": cached.model,
                "provider": cached.provider,
                "input_tokens": cached.input_tokens,
                "output_tokens": cached.output_tokens,
            })
        return out

    if cache == "hit":
        description = (
            "Look up the in-memory F-022 response cache for this feature prompt. "
            "Returns cache=hit with model metadata when a stored LLM answer is reused."
        )
    else:
        description = (
            "Look up the in-memory F-022 response cache for this feature prompt. "
            "Returns cache=miss when no stored answer exists and the LLM must run."
        )
    return StructuredTool.from_function(
        func=check_response_cache,
        name=RESPONSE_CACHE_TOOL_NAME,
        description=description,
        args_schema=_CheckResponseCacheInput,
    )


def _trace_config_for_cache(*, cache: Literal["hit", "miss", "disabled"], cached: LLMResult | None = None):
    """Metadata `response_cache` no config corrente; None se não há trace ativo."""
    config = current_runnable_config()
    if not config or not config.get("callbacks"):
        return None
    meta = dict(config.get("metadata") or {})
    meta["response_cache"] = cache
    if cache == "hit" and cached is not None:
        meta.update({
            "model": cached.model,
            "provider": cached.provider,
            "input_tokens": cached.input_tokens,
            "output_tokens": cached.output_tokens,
        })
    return {**config, "metadata": meta}


def _emit_cache_hit_trace(feature: str, prompt: str, cached: LLMResult) -> None:
    """Replay sintético no trace quando F-022 devolve hit (DT-018 / F-GALILEO-9).

    No hit não há `model.invoke`, então não nasce LLM span. Emite chain `feature.{step}` com
    **tool span** `check_response_cache` (StructuredTool — decisão visível como tool no Console)
    e replay da resposta cacheada como output da chain. Só emite se já existe trace em
    andamento — nunca fabrica um trace órfão."""
    trace_config = _trace_config_for_cache(cache="hit", cached=cached)
    if trace_config is None:
        return
    try:
        run_name = _feature_run_name(feature)
        cache_tool = _make_check_response_cache_tool(feature, cache="hit", cached=cached)
        replay_name = response_cache_replay_run_name(run_name)

        def replay_cached(_lookup: dict) -> str:
            return cached.text

        chain = (
            cache_tool
            | RunnableLambda(replay_cached, name=replay_name).with_config(
                {"run_name": replay_name, "name": replay_name},
            )
        ).with_config({"run_name": run_name, "name": run_name})
        chain.invoke({"input": prompt}, config=trace_config)
    except Exception:  # noqa: BLE001 — observabilidade não derruba a resposta
        pass


def build_cache_miss_chain(
    feature: str,
    llm_runnable: Runnable,
    *,
    prep: Runnable | None = None,
    provider: str = "",
) -> Runnable:
    """Monta chain LCEL miss: [prep |] check_response_cache | llm — um único `invoke` (F-GALILEO-17).

    D.2: `check_response_cache` vira metadata no span ancestral (política de supressão em
    `galileo_span_policy`) — o `invoke_llm` sobrevivente é o único span que resta pra carregar
    a identidade do attempt, daí `llm_attempt`/`llm_provider` irem aqui via `with_config`."""
    run_name = _feature_run_name(feature)
    invoke_name = response_cache_invoke_run_name(run_name)

    def _state_for_llm(state: dict) -> dict:
        return {k: v for k, v in state.items() if k not in ("cache", "feature")}

    llm_segment = (
        RunnableLambda(_state_for_llm, name=invoke_name).with_config(
            {"run_name": invoke_name, "name": invoke_name},
        )
        | llm_runnable
    ).with_config({
        "run_name": invoke_name,
        "name": invoke_name,
        "metadata": {"llm_attempt": 1, "llm_provider": provider},
    })

    if not cache_globally_enabled():
        core: Runnable = llm_segment.with_config({"run_name": run_name, "name": run_name})
        if prep is not None:
            core = (prep | core).with_config({"run_name": run_name, "name": run_name})
        return core.with_config({"run_name": run_name, "name": run_name})

    cache_tool = _make_check_response_cache_tool(feature, cache="miss")

    def _cache_miss_with_passthrough(state: dict, config: RunnableConfig | None = None) -> dict:
        """Tool span + preserva `system_context` e demais campos do prep."""
        meta = cache_tool.invoke({"input": state["input"]}, config=config)
        return {**state, **meta}

    core = (
        RunnableLambda(
            _cache_miss_with_passthrough, name=RESPONSE_CACHE_TOOL_NAME,
        ).with_config({"run_name": RESPONSE_CACHE_TOOL_NAME, "name": RESPONSE_CACHE_TOOL_NAME})
        | llm_segment
    ).with_config({"run_name": run_name, "name": run_name})
    if prep is not None:
        core = (prep | core).with_config({"run_name": run_name, "name": run_name})
    return core.with_config({"run_name": run_name, "name": run_name})


def _invoke_with_cache_miss_trace(feature: str, prompt: str, invoke_fn):
    """Encadeia `check_response_cache` (cache=miss) antes da chain LLM real (F-GALILEO-10).

    Simetria com o hit: o Console mostra a decisão de cache antes do LLM span. Sem callbacks
    ativos, delega direto a `invoke_fn()` — zero overhead offline."""
    if not cache_globally_enabled():
        return invoke_fn()
    trace_config = _trace_config_for_cache(cache="miss")
    if trace_config is None:
        return invoke_fn()
    try:
        run_name = _feature_run_name(feature)
        cache_tool = _make_check_response_cache_tool(feature, cache="miss")
        invoke_name = response_cache_invoke_run_name(run_name)

        def run_llm(_lookup: dict):
            return invoke_fn()

        chain = (
            cache_tool
            | RunnableLambda(run_llm, name=invoke_name).with_config(
                {"run_name": invoke_name, "name": invoke_name},
            )
        ).with_config({"run_name": run_name, "name": run_name})
        return chain.invoke({"input": prompt}, config=trace_config)
    except Exception:  # noqa: BLE001 — observabilidade não derruba a resposta
        return invoke_fn()


def invoke_cached_chain(
    feature: str,
    prompt: str,
    model_key: str,
    miss_chain: Runnable,
    miss_input: dict,
    *,
    to_llm_result,
    system: str = "",
    max_tokens: int | None = None,
    verbose: bool = False,
    degrade_fn=None,
    use_cache: bool = True,
    config=None,
):
    """Como `invoke_cached`, mas no miss executa `miss_chain` LCEL inteira (retrieval + cache + LLM)."""

    def _run_miss(cfg):
        if cfg is None:
            return to_llm_result(miss_chain.invoke(miss_input))
        return to_llm_result(miss_chain.invoke(miss_input, config=cfg))

    effective = cache_enabled_for(feature, use_cache)
    if not effective:
        if not _limiter.allow():
            if degrade_fn is None:
                raise RuntimeError("rate_limited sem degrade_fn")
            return degrade_fn(), "rate_limited"
        trace_config = _trace_config_for_cache(cache="disabled")
        if config is not None and config.get("callbacks"):
            merged = dict(config)
            merged_meta = dict((config.get("metadata") or {}))
            merged_meta["response_cache"] = "disabled"
            merged["metadata"] = merged_meta
            trace_config = merged
        return _run_miss(trace_config), "miss"

    key = make_cache_key(
        feature, prompt, model_key,
        system=system, max_tokens=max_tokens, verbose=verbose,
    )
    cached = _cache.get(key)
    if cached is not None:
        _emit_cache_hit_trace(feature, prompt, cached)
        return cached, "hit"

    with _inflight.get(key):
        cached = _cache.get(key)
        if cached is not None:
            _emit_cache_hit_trace(feature, prompt, cached)
            return cached, "hit"
        if not _limiter.allow():
            if degrade_fn is None:
                raise RuntimeError("rate_limited sem degrade_fn")
            return degrade_fn(), "rate_limited"
        trace_config = _trace_config_for_cache(cache="miss")
        if config is not None and config.get("callbacks"):
            merged = dict(config)
            merged_meta = dict((config.get("metadata") or {}))
            merged_meta["response_cache"] = "miss"
            merged["metadata"] = merged_meta
            trace_config = merged
        try:
            result = _run_miss(trace_config)
        except Exception:  # noqa: BLE001 — observabilidade não derruba a resposta
            result = to_llm_result(miss_chain.invoke(miss_input, config=config))
        _cache.put(key, result)
        return result, "miss"


def invoke_cached(feature: str, prompt: str, model_key: str, invoke_fn, *,
                  system: str = "", max_tokens: int | None = None, verbose: bool = False,
                  degrade_fn=None, use_cache: bool = True):
    """Executa `invoke_fn()` com cache + single-flight + rate-limit. Devolve
    `(LLMResult, status)` com `status ∈ {"hit", "miss", "rate_limited"}`. A chave de cache usa
    `model_key` + system/max_tokens/verbose conhecidos ANTES da chamada.

    Ordem: cache → single-flight (serializa idênticas) → recheck → rate-limit → provider → cache.
    Assim chamadas idênticas concorrentes dedupam ANTES de consumir orçamento de rate-limit."""
    effective = cache_enabled_for(feature, use_cache)
    if not effective:
        if not _limiter.allow():
            if degrade_fn is None:
                raise RuntimeError("rate_limited sem degrade_fn")
            return degrade_fn(), "rate_limited"
        _trace_config_for_cache(cache="disabled")
        return invoke_fn(), "miss"

    key = make_cache_key(
        feature, prompt, model_key,
        system=system, max_tokens=max_tokens, verbose=verbose,
    )
    cached = _cache.get(key)
    if cached is not None:
        _emit_cache_hit_trace(feature, prompt, cached)
        return cached, "hit"

    with _inflight.get(key):  # single-flight: idênticas concorrentes esperam aqui
        cached = _cache.get(key)  # outra thread idêntica pode ter preenchido enquanto esperávamos
        if cached is not None:
            _emit_cache_hit_trace(feature, prompt, cached)
            return cached, "hit"
        if not _limiter.allow():
            if degrade_fn is None:
                raise RuntimeError("rate_limited sem degrade_fn")
            return degrade_fn(), "rate_limited"  # NÃO cacheia degradação (é transitória)
        result = _invoke_with_cache_miss_trace(feature, prompt, invoke_fn)
        _cache.put(key, result)
        return result, "miss"


def complete_cached(llm, feature: str, system: str, prompt: str, *,
                    max_tokens: int | None = None, verbose: bool = False, use_cache: bool = True):
    """Wrapper legado sobre `invoke_cached` para `llm.complete` (F-022 smoke / adapters antigos)."""
    model_key = llm.primary_model()

    def invoke_fn():
        return llm.complete(system, prompt, verbose=verbose, max_tokens=max_tokens)

    def degrade_fn():
        return StubLLM(model_key).complete(system, prompt, verbose=verbose, max_tokens=max_tokens)

    return invoke_cached(
        feature, prompt, model_key, invoke_fn,
        system=system, max_tokens=max_tokens, verbose=verbose,
        degrade_fn=degrade_fn, use_cache=use_cache,
    )


def clear_cache() -> None:
    """Limpa o cache de resposta (uso em teste / reset entre turmas / update de agente)."""
    _cache.clear()


def reset_state() -> None:
    """Zera cache + rate-limit (isolamento entre testes)."""
    global _cache, _inflight, _limiter
    _cache = ResponseCache()
    _inflight = _KeyedLocks()
    _limiter = RateLimiter()
