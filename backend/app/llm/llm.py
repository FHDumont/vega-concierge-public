"""Multi-provider LLM client with cascading fallback (F-020).

Real adapters (OpenAI-compatible and Anthropic) + offline StubLLM as last resort.
Cascade tries enabled providers in order; on failure (error/timeout/rate-limit)
falls to the next; StubLLM ensures the app continues standalone without any key.

Provider is resolved PER CALL from current config, not fixed at import — config changes apply
without restart. WHICH config to use and in what order is decided by `llm_providers` (single base,
F-BACKEND-1); this module only knows HOW TO TALK to each provider.

HTTP via official SDKs (`openai`, `anthropic`) — F-OBS-PREP-1 / ADR-027; readiness for
Splunk Agent Observability/OTel. API keys are SECRETS: never logged or returned.
"""
import random
import time
from dataclasses import dataclass

from anthropic import Anthropic, AnthropicBedrock
from openai import OpenAI

from .http_ssl import sync_http_client
from .llm_providers import bedrock_region, load_provider_configs, resolve_provider_configs
from ..settings import settings

# Timeout per provider call (s). Short so cascade falls to fallback quickly.
LLM_TIMEOUT_S = settings.llm_timeout_s
DEFAULT_STUB_MODEL = settings.llm_stub_model


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str = "stub"   # friendly name of the provider that responded
    system: str = "stub"     # provider family (openai | anthropic | stub)
    fallback: bool = False   # True if a prior provider in cascade failed before this one
    prompt_cache_tokens: int = 0  # input tokens covered by provider prompt-cache (F-COST-CACHE)


# --- Adapters ---------------------------------------------------------------

class StubLLM:
    """Deterministic-ish, generates plausible tokens without network call. Last resort
    in cascade — keeps app working offline without any key configured."""
    name = "stub"
    system = "stub"

    def __init__(self, model: str = DEFAULT_STUB_MODEL):
        self.model = model

    def complete(self, system: str, prompt: str, verbose: bool = False, max_tokens: int | None = None) -> LLMResult:
        time.sleep(0.05)
        out = f"[stub:{self.model}] resposta para: {prompt[:48]}"
        in_tok = max(8, len(system.split()) + len(prompt.split()))
        # `max_tokens` (cap per feature — F-022) limits synthetic size; otherwise uses default.
        out_tok = (max_tokens or (120 if verbose else 30)) + random.randint(0, 10)
        return LLMResult(out, in_tok, out_tok, self.model, provider=self.name, system=self.system)


class OpenAICompatAdapter:
    """OpenAI-compatible Chat Completions: covers OpenAI, Groq, xAI/Grok, OpenRouter, and
    the like (just `base_url` + `api_key` + `model`). Real tokens/model from response."""
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
    """Anthropic Messages API (Claude). Official `base_url` default; real tokens from response.
    Alternative (spec decision pending): route Claude via OpenRouter using OpenAI-compatible adapter
    — we support BOTH paths (ADR-015)."""
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


def make_bedrock_client(region: str, api_key: str, model: str, timeout: float = LLM_TIMEOUT_S):
    """Bedrock client via API key (UI). Uses AnthropicBedrock (bedrock-runtime) — Mantle requires IAM."""
    if not api_key:
        raise ValueError("Bedrock provider requires api_key (Bedrock API key from Admin UI)")
    return AnthropicBedrock(
        aws_region=region,
        timeout=timeout,
        http_client=sync_http_client(timeout),
        api_key=api_key,
    )


class BedrockAdapter:
    """Amazon Bedrock (Claude via Anthropic SDK). `base_url` = AWS region; `api_key` = Bedrock API key
    registered in UI (workshop does not use IAM/credential chain at runtime)."""
    system = "bedrock"

    def __init__(self, name: str, base_url: str, api_key: str, model: str):
        self.name = name
        self.region = bedrock_region(base_url)
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


# config kind → adapter class. Add another provider = one line here.
_ADAPTERS = {"openai": OpenAICompatAdapter, "anthropic": AnthropicAdapter, "bedrock": BedrockAdapter}


def build_adapter(cfg: dict):
    """Build adapter from config dict. Returns None if kind/model/api_key is missing."""
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


# --- Cascade ---------------------------------------------------------------

class CascadeLLM:
    """Tries adapters in order; on failure (error/timeout/rate-limit) falls to next.
    Last adapter is always StubLLM → app never goes without response (standalone-first)."""

    def __init__(self, adapters: list):
        self.adapters = adapters  # ordered; StubLLM last

    def primary_model(self) -> str:
        """Model of highest-priority adapter (1st in cascade) — used as part of cache KEY
        (F-022) BEFORE call. Stub last ensures always a value."""
        return self.adapters[0].model if self.adapters else DEFAULT_STUB_MODEL

    def complete(self, system: str, prompt: str, verbose: bool = False, max_tokens: int | None = None) -> LLMResult:
        last_err = None
        for i, adapter in enumerate(self.adapters):
            try:
                r = adapter.complete(system, prompt, verbose=verbose, max_tokens=max_tokens)
                r.fallback = i > 0  # some earlier provider failed before this one answered
                return r
            except Exception as e:  # noqa: BLE001 — any failure drops to next (no logging: msg may cite key)
                last_err = e
                continue
        # Unreachable in practice (StubLLM doesn't fail), but be explicit.
        raise RuntimeError(f"all cascade providers failed: {type(last_err).__name__}")


# --- Config resolution (source evolves; consumers don't) ------------------

def get_llm() -> CascadeLLM:
    """Cascade resolved from CURRENT config (per call, not at import) — config changes apply
    without restart. Always ends with StubLLM (offline last resort)."""
    adapters = [a for a in (build_adapter(c) for c in load_provider_configs()) if a is not None]
    adapters.append(StubLLM())
    return CascadeLLM(adapters)


def get_llm_for(connection: str = "", model: str = "") -> CascadeLLM:
    """LLM resolved for ONE agent (F-021). `connection` = provider id to pin (or '' =
    full cascade); `model` = optional model override. Always ends with StubLLM —
    pinning to disabled/absent connection falls to stub (offline). Without connection or model
    is equivalent to `get_llm()` (current cascade)."""
    cfgs = resolve_provider_configs(connection, model)
    adapters = [a for a in (build_adapter(c) for c in cfgs) if a is not None]
    adapters.append(StubLLM(model or DEFAULT_STUB_MODEL))
    return CascadeLLM(adapters)


def test_provider(cfg: dict) -> dict:
    """Test call to ONE provider (config 'Test' button — F-020 stage 3). Measures latency
    using real model/tokens. Does NOT log or echo key; on error returns type+message (adapter
    exceptions don't contain key, which only goes in header)."""
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
    """Test per agent (F-021): resolve agent's LLM (`get_llm_for`) and make ONE real call
    with effective system prompt. Since cascade always ends in stub, `ok` is True even offline
    — `provider`/`model` show what agent would ACTUALLY use (stub if nothing configured/down)."""
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
