"""LLM cost control layer (F-022) — transversal to ALL AI calls from features.

So "AI everywhere in app" doesn't explode tokens, every feature goes through here:
- **Response cache** by key `(feature, normalized input, model, system_hash, max_tokens, verbose)`, with configurable TTL.
  In-memory (enough for VM, 1 user; resets on restart — spec decision, not SQLite).
- **single-flight**: identical concurrent calls (e.g., simulator) deduplicate — only ONE goes to
  provider, others reuse result. (per-key locks).
- **rate-limit per instance**: sliding window; on overflow, degrades to StubLLM (offline,
  no cost) instead of breaking — standalone-first. Marks reason in status.
- **max_tokens** per feature: cap goes direct to adapter (see `llm.py`).

Doesn't resolve config — caller (`agents.feature_complete`) uses result (hit|miss). No
new dependency; thread-safe (sync endpoints run in threadpool).
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
from ..rate_limit import RateLimiter
from ..runnable_config import current_runnable_config
from ..settings import settings

# Knobs per env (convenient defaults for workshop; generous rate to not interfere with normal use,
# but contain simulator floods / cost_spike). Cache TTL in seconds.
CACHE_TTL_S = settings.llm_cache_ttl_s
CACHE_MAX = settings.llm_cache_max
RATE_MAX = settings.llm_rate_max              # number of real provider calls...
RATE_WINDOW_S = settings.llm_rate_window_s    # ...per window (s); <=0 disables.


def cache_globally_enabled() -> bool:
    """True when `LLM_CACHE_ENABLED` is on (default `1`). Falsy: 0/false/no/off."""
    return settings.llm_cache_enabled


# UC-5 (F-WORKSHOP-STAB-4): cache hit doesn't invoke model → without `model.invoke` no
# `[llm]` span is born, and without span PII/tone evaluators have nothing to assess. Declarative choke point
# instead of sprinkling `use_cache=False` per call site — mirrors `galileo_control.CONTROL_FEATURES_POST`
# (`obs/galileo_control.py:25`) without inverting layer (`llm/` doesn't import `obs/`). Scope limited
# to two post/Steer targets for now: `product_qa`/`search` (pre) are most-called features
# in store and cache key already includes question — include only if real hit appears in UC-1.
NO_RESPONSE_CACHE_FEATURES: frozenset[str] = frozenset({"notification_copy"})


def cache_enabled_for(feature: str, use_cache: bool) -> bool:
    return use_cache and cache_globally_enabled() and feature not in NO_RESPONSE_CACHE_FEATURES


def normalize(text: str) -> str:
    """Normalize input for cache key: collapse spaces + lowercase (hits robust to
    trivial typing/whitespace variations)."""
    return " ".join((text or "").split()).lower()


def system_hash(system: str) -> str:
    """Short hash of system prompt (F-COST-CACHE): change system → clean miss, no stale."""
    return hashlib.sha256(normalize(system).encode("utf-8")).hexdigest()[:16]


def make_cache_key(feature: str, prompt: str, model_key: str, *,
                   system: str = "", max_tokens: int | None = None, verbose: bool = False):
    """Stable key: feature + prompt norm + model + system_hash + max_tokens + verbose."""
    return (
        feature,
        normalize(prompt),
        model_key,
        system_hash(system),
        int(max_tokens) if max_tokens is not None else 0,
        bool(verbose),
    )


class ResponseCache:
    """Simple TTL cache (dict + lock). Arbitrary key → (expires_at, value). Eviction: on
    fill, discards expired entries and, if still full, the oldest (approximate FIFO)."""

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
        if len(self._d) >= self.maxsize:  # still full → discard oldest inserted
            oldest = next(iter(self._d), None)
            if oldest is not None:
                self._d.pop(oldest, None)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()

    def __len__(self) -> int:
        return len(self._d)


class _KeyedLocks:
    """Map of per-key locks for single-flight: serializes identical concurrent calls."""

    def __init__(self):
        self._locks: dict = {}
        self._guard = threading.Lock()

    def get(self, key) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
            return lock


# Per-instance singletons (1 backend per VM). Swappable in test via reset_state().
_cache = ResponseCache()
_inflight = _KeyedLocks()
_limiter = RateLimiter(RATE_MAX, RATE_WINDOW_S)


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
    """Shared StructuredTool — F-022 decision visible as tool span in Console."""

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
    """Metadata `response_cache` in current config; None if no active trace."""
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
    """Synthetic replay in trace when F-022 returns hit (DT-018 / F-GALILEO-9).

    On hit there's no `model.invoke`, so no LLM span is born. Emits chain `feature.{step}` with
    **tool span** `check_response_cache` (StructuredTool — decision visible as tool in Console)
    and replay of cached response as chain output. Only emits if trace already in progress
    — never fabricates orphan trace."""
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
    except Exception:  # noqa: BLE001 — observability doesn't break response
        pass


def build_cache_miss_chain(
    feature: str,
    llm_runnable: Runnable,
    *,
    prep: Runnable | None = None,
    provider: str = "",
) -> Runnable:
    """Build LCEL miss chain: [prep |] check_response_cache | llm — single `invoke` (F-GALILEO-17).

    D.2: `check_response_cache` becomes metadata in ancestor span (suppression policy in
    `galileo_span_policy`) — the surviving `invoke_llm` is the only span left to carry
    attempt identity, hence `llm_attempt`/`llm_provider` go here via `with_config`."""
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
        """Tool span + preserves `system_context` and other prep fields."""
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
    """Chain `check_response_cache` (cache=miss) before real LLM chain (F-GALILEO-10).

    Symmetry with hit: Console shows cache decision before LLM span. Without active callbacks,
    delegates directly to `invoke_fn()` — zero offline overhead."""
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
    except Exception:  # noqa: BLE001 — observability doesn't break response
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
    """Like `invoke_cached`, but on a miss runs the entire `miss_chain` LCEL (retrieval + cache + LLM)."""

    def _run_miss(cfg):
        if cfg is None:
            return to_llm_result(miss_chain.invoke(miss_input))
        return to_llm_result(miss_chain.invoke(miss_input, config=cfg))

    effective = cache_enabled_for(feature, use_cache)
    if not effective:
        if not _limiter.allow():
            if degrade_fn is None:
                raise RuntimeError("rate_limited without degrade_fn")
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
                raise RuntimeError("rate_limited without degrade_fn")
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
        except Exception:  # noqa: BLE001 — observability must not break the response
            result = to_llm_result(miss_chain.invoke(miss_input, config=config))
        _cache.put(key, result)
        return result, "miss"


def invoke_cached(feature: str, prompt: str, model_key: str, invoke_fn, *,
                  system: str = "", max_tokens: int | None = None, verbose: bool = False,
                  degrade_fn=None, use_cache: bool = True):
    """Execute `invoke_fn()` with cache + single-flight + rate-limit. Returns
    `(LLMResult, status)` with `status ∈ {"hit", "miss", "rate_limited"}`. Cache key uses
    `model_key` + system/max_tokens/verbose known BEFORE call.

    Order: cache → single-flight (serializes identical) → recheck → rate-limit → provider → cache.
    So identical concurrent calls deduplicate BEFORE consuming rate-limit budget."""
    effective = cache_enabled_for(feature, use_cache)
    if not effective:
        if not _limiter.allow():
            if degrade_fn is None:
                raise RuntimeError("rate_limited without degrade_fn")
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

    with _inflight.get(key):  # single-flight: identical concurrent ones wait here
        cached = _cache.get(key)  # another identical thread may have filled while we waited
        if cached is not None:
            _emit_cache_hit_trace(feature, prompt, cached)
            return cached, "hit"
        if not _limiter.allow():
            if degrade_fn is None:
                raise RuntimeError("rate_limited without degrade_fn")
            return degrade_fn(), "rate_limited"  # DON'T cache degradation (it's transitory)
        result = _invoke_with_cache_miss_trace(feature, prompt, invoke_fn)
        _cache.put(key, result)
        return result, "miss"


def complete_cached(llm, feature: str, system: str, prompt: str, *,
                    max_tokens: int | None = None, verbose: bool = False, use_cache: bool = True):
    """Legacy wrapper over `invoke_cached` for `llm.complete` (F-022 smoke / old adapters)."""
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
    """Clear response cache (use in test / reset between sessions / agent update)."""
    _cache.clear()


def reset_state() -> None:
    """Zero cache + rate-limit (isolation between tests)."""
    global _cache, _inflight, _limiter
    _cache = ResponseCache()
    _inflight = _KeyedLocks()
    _limiter = RateLimiter(RATE_MAX, RATE_WINDOW_S)


def llm_rate_allow() -> bool:
    """Budget for real provider calls in LLM window (F-WORKSHOP-GUARD)."""
    return _limiter.allow()
