"""Choke point LangChain para LLM (F-OBS-PREP-1 / ADR-027).

Resolve config por agente → `ChatOpenAI` / `ChatAnthropic` / stub offline. Caminho de agentes
usa `invoke` (não `llm.complete`). Cascata de fallback espelha `get_llm_for` / `get_llm`.
Sem imports de galileo/opentelemetry/openinference nesta fase.
"""
from __future__ import annotations

import re
from functools import cached_property
from pydantic import ConfigDict, Field

from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..galileo_span import default_llm_run_name
from .http_ssl import async_http_client, sync_http_client
from .llm import DEFAULT_STUB_MODEL, LLM_TIMEOUT_S, LLMResult, make_bedrock_client
from .llm_cache import llm_rate_allow
from .llm_providers import bedrock_region, provider_configs_for_agent
from ..settings import settings
from .stub import VegaStubChatModel, make_stub_chat_model

# Prompt-cache do *provider* (Anthropic cache_control) — orthogonal ao cache F-022 de resposta.
# Default off: lab multi-gateway; ligar só com Claude real. OpenAI cacheia prefixo automaticamente.
_PROVIDER_PROMPT_CACHE_ON = settings.llm_provider_prompt_cache


def provider_prompt_cache_enabled() -> bool:
    """True se LLM_PROVIDER_PROMPT_CACHE pede cache_control no system (só Anthropic)."""
    return _PROVIDER_PROMPT_CACHE_ON


def make_system_message(model: BaseChatModel, system: str) -> SystemMessage:
    """SystemMessage; com flag on + family anthropic → content block com cache_control ephemeral.

    Não aplica a openai/stub/openai-compat (evita campos desconhecidos em gateways).
    OpenAI oficial já faz prompt caching automático acima do limiar — só métricas (P4a)."""
    text = system or ""
    if provider_prompt_cache_enabled():
        _, family, _ = _model_identity(model)
        if family == "anthropic" and text:
            return SystemMessage(content=[{
                "type": "text",
                "text": text,
                "cache_control": {"type": "ephemeral"},
            }])
    return SystemMessage(content=text)


# Falha de um provider na cascata: (provider, family, model, mensagem crua sanitizada).
# `family` viaja junto p/ que o LLMResult de erro carregue a família REAL do provider que
# falhou (anthropic/bedrock/openai), em vez de um default fixo.
CascadeError = tuple[str, str, str, str]


def is_stub_output(text: str) -> bool:
    """True se o texto veio do stub offline (`[stub]` / `[stub:model]`)."""
    return (text or "").strip().startswith("[stub")


def format_llm_provider_error(errors: list[CascadeError]) -> str:
    """Mensagem legível p/ o shopper quando a cascata de providers falhou antes do stub.

    Agnóstica de provider: nomeia quem falhou e aponta o Admin. O mesmo texto serve p/ OpenAI,
    Anthropic, Bedrock, Groq ou Ollama — nenhum provider tem instrução própria embutida aqui."""
    if not errors:
        return (
            "The AI assistant is temporarily unavailable. "
            "Check Admin → LLM Providers and try again."
        )
    provider, _family, model, raw = errors[0]
    low = raw.lower()
    missing = model
    m = re.search(r"model ['\"]?([^'\"]+)['\"]? not found", raw, re.I)
    if m:
        missing = m.group(1)
    if ("not found" in low and "model" in low) or "404" in raw:
        return (
            f"The AI provider {provider} could not find model {missing}. "
            "Check the model name in Admin → LLM Providers."
        )
    if "connection refused" in low or "failed to connect" in low or "connecterror" in low:
        return (
            f"I couldn't connect to {provider}. "
            "Check that it is reachable and that the base URL in Admin → LLM Providers is correct."
        )
    if "401" in raw or "403" in raw or "unauthorized" in low or "authentication" in low:
        return (
            f"The AI provider {provider} rejected the request (authentication error). "
            "Check the API key and base URL in Admin → LLM Providers."
        )
    detail = _sanitize_trace_text(raw.strip(), max_len=160)
    return (
        f"The AI assistant couldn't complete your request ({provider}/{model}). "
        f"{detail} Check Admin → LLM Providers."
    )


def apply_cascade_stub_policy(text: str, errors: list[CascadeError]) -> str:
    """Substitui eco stub (`[stub] sua pergunta…`) por erro legível quando providers reais falharam."""
    if errors and is_stub_output(text):
        return format_llm_provider_error(errors)
    return text


_LLM_UNAVAILABLE_PREFIXES = (
    "I couldn't connect to",
    "The AI provider",
    "The AI assistant couldn't complete",
    "The AI assistant is temporarily unavailable",
)


def is_llm_unavailable_reply(text: str) -> bool:
    """True quando a resposta é erro de provider/cascata — não deve renderizar artefatos de loja."""
    t = (text or "").strip()
    if is_stub_output(t):
        return True
    return any(t.startswith(p) for p in _LLM_UNAVAILABLE_PREFIXES)


def _record_cascade_error(errors: list[CascadeError], model: BaseChatModel, exc: Exception) -> None:
    provider, family, model_key = _model_identity(model)
    errors.append((provider, family, model_key, _sanitize_trace_text(str(exc), max_len=500)))


def _sanitize_trace_text(text: str, *, max_len: int = 8000) -> str:
    """Remove caracteres que quebram serialização/UI de traces (Splunk Agent Observability Console)."""
    if not text:
        return ""
    cleaned = "".join(c if c.isprintable() or c in "\n\t" else " " for c in str(text))
    if len(cleaned) > max_len:
        return cleaned[: max_len - 1] + "…"
    return cleaned


def invoke_bind_tools_cascade(
    agent_name: str,
    *,
    tools: list,
    system: str,
    system_messages: list[BaseMessage],
    lc_messages: list[BaseMessage],
    config=None,
    run_name: str,
    verbose: bool = False,
) -> tuple[AIMessage, BaseChatModel, list[CascadeError]]:
    """Cascata bind_tools: tenta providers reais (tokens cloud) → stub offline no fim."""
    models = resolve_chat_models(agent_name)
    errors: list[CascadeError] = []
    response: AIMessage | None = None
    model_used: BaseChatModel = models[0] if models else make_stub_chat_model()
    last_err: Exception | None = None

    for i, candidate in enumerate(models):
        if i == 0:
            candidate = get_chat_model(agent_name)
        model_used = candidate
        invoke_messages = [
            make_system_message(candidate, system), *system_messages, *lc_messages,
        ]
        bound = candidate.bind_tools(tools)
        bound = _with_run_name(bound, candidate, run_name)
        try:
            if isinstance(candidate, VegaStubChatModel):
                response = bound.invoke(invoke_messages, config=config, verbose=verbose)
            else:
                response = bound.invoke(invoke_messages, config=config)
            if not isinstance(response, AIMessage):
                response = AIMessage(content=str(getattr(response, "content", response)))
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            _record_cascade_error(errors, candidate, e)
            response = None

    if response is None:
        if errors:
            response = AIMessage(content=format_llm_provider_error(errors))
            # Falhou tudo: o último candidato é o stub, mas quem falhou foi o provider primário —
            # é ele que precisa aparecer no Inspector/telemetria.
            if models:
                model_used = models[0]
        else:
            raise RuntimeError(f"bind_tools cascade failed: {type(last_err).__name__}")

    text = response.content if isinstance(response.content, str) else str(response.content)
    text = apply_cascade_stub_policy(text, errors)
    if text != (response.content if isinstance(response.content, str) else str(response.content)):
        response = AIMessage(content=text)
    return response, model_used, errors


async def ainvoke_bind_tools_cascade(
    agent_name: str,
    *,
    tools: list,
    system: str,
    system_messages: list[BaseMessage],
    lc_messages: list[BaseMessage],
    config=None,
    run_name: str,
    verbose: bool = False,
) -> tuple[AIMessage, BaseChatModel, list[CascadeError]]:
    """Async bind_tools cascade — mesma política que invoke_bind_tools_cascade."""
    models = resolve_chat_models(agent_name)
    errors: list[CascadeError] = []
    response: AIMessage | None = None
    model_used: BaseChatModel = models[0] if models else make_stub_chat_model()
    last_err: Exception | None = None

    for i, candidate in enumerate(models):
        if i == 0:
            candidate = get_chat_model(agent_name)
        model_used = candidate
        invoke_messages = [
            make_system_message(candidate, system), *system_messages, *lc_messages,
        ]
        bound = candidate.bind_tools(tools)
        bound = _with_run_name(bound, candidate, run_name)
        try:
            if isinstance(candidate, VegaStubChatModel):
                response = await bound.ainvoke(invoke_messages, config=config, verbose=verbose)
            else:
                response = await bound.ainvoke(invoke_messages, config=config)
            if not isinstance(response, AIMessage):
                response = AIMessage(content=str(getattr(response, "content", response)))
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            _record_cascade_error(errors, candidate, e)
            response = None

    if response is None:
        if errors:
            response = AIMessage(content=format_llm_provider_error(errors))
            # Falhou tudo: o último candidato é o stub, mas quem falhou foi o provider primário —
            # é ele que precisa aparecer no Inspector/telemetria.
            if models:
                model_used = models[0]
        else:
            raise RuntimeError(f"bind_tools cascade failed: {type(last_err).__name__}")

    text = response.content if isinstance(response.content, str) else str(response.content)
    text = apply_cascade_stub_policy(text, errors)
    if text != (response.content if isinstance(response.content, str) else str(response.content)):
        response = AIMessage(content=text)
    return response, model_used, errors


class VegaChatAnthropic(ChatAnthropic):
    """ChatAnthropic com trust store do OS — langchain-anthropic 1.4.x não aceita http_client no ctor."""

    @cached_property
    def _client(self) -> anthropic.Client:
        timeout = self._client_params.get("timeout") or LLM_TIMEOUT_S
        return anthropic.Client(
            **self._client_params,
            http_client=sync_http_client(timeout),
        )

    @cached_property
    def _async_client(self) -> anthropic.AsyncClient:
        timeout = self._client_params.get("timeout") or LLM_TIMEOUT_S
        return anthropic.AsyncClient(
            **self._client_params,
            http_client=async_http_client(timeout),
        )


class VegaBedrockChatModel(VegaChatAnthropic):
    """ChatAnthropic apontando p/ AnthropicBedrock(Mantle) — ReAct/bind_tools intactos."""

    region: str = "us-east-1"
    bedrock_api_key: str = ""

    @cached_property
    def _client(self) -> anthropic.Client:
        timeout = self._client_params.get("timeout") or LLM_TIMEOUT_S
        model = str(getattr(self, "model", None) or getattr(self, "model_name", "") or "")
        return make_bedrock_client(self.region, self.bedrock_api_key, model, timeout)

    @cached_property
    def _async_client(self) -> anthropic.AsyncClient:
        from anthropic import AsyncAnthropicBedrock

        timeout = self._client_params.get("timeout") or LLM_TIMEOUT_S
        if not self.bedrock_api_key:
            raise ValueError("Bedrock provider requires api_key (Bedrock API key from Admin UI)")
        return AsyncAnthropicBedrock(
            aws_region=self.region,
            timeout=timeout,
            http_client=async_http_client(timeout),
            api_key=self.bedrock_api_key,
        )


def build_chat_model(cfg: dict, *, llm_run_name: str | None = None) -> BaseChatModel | None:
    """Monta ChatOpenAI, ChatAnthropic ou VegaBedrockChatModel a partir de um dict de provider."""
    kind = cfg.get("kind", "openai")
    if not cfg.get("model") or not cfg.get("api_key"):
        return None
    provider_label = cfg.get("name", cfg.get("kind", "?"))
    llm_name = llm_run_name
    if kind == "openai":
        base_url = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        kwargs: dict[str, Any] = {
            "model": cfg["model"],
            "api_key": cfg["api_key"],
            "base_url": base_url,
            "timeout": LLM_TIMEOUT_S,
            "http_client": sync_http_client(LLM_TIMEOUT_S),
        }
        if llm_name:
            kwargs["name"] = llm_name
        model = ChatOpenAI(**kwargs)
        model._vega_provider = provider_label  # type: ignore[attr-defined]
        model._vega_family = "openai"  # type: ignore[attr-defined]
        return model
    if kind == "anthropic":
        base_url = (cfg.get("base_url") or "https://api.anthropic.com").rstrip("/")
        kwargs = {
            "model": cfg["model"],
            "api_key": cfg["api_key"],
            "base_url": base_url,
            "timeout": LLM_TIMEOUT_S,
        }
        if llm_name:
            kwargs["name"] = llm_name
        model = VegaChatAnthropic(**kwargs)
        model._vega_provider = provider_label  # type: ignore[attr-defined]
        model._vega_family = "anthropic"  # type: ignore[attr-defined]
        return model
    if kind == "bedrock":
        region = bedrock_region(cfg.get("base_url", ""))
        kwargs = {
            "model": cfg["model"],
            "anthropic_api_key": cfg["api_key"],
            "timeout": LLM_TIMEOUT_S,
            "region": region,
            "bedrock_api_key": cfg["api_key"],
        }
        if llm_name:
            kwargs["name"] = llm_name
        model = VegaBedrockChatModel(**kwargs)
        model._vega_provider = provider_label  # type: ignore[attr-defined]
        model._vega_family = "bedrock"  # type: ignore[attr-defined]
        return model
    return None


def resolve_chat_models(agent_name: str = "") -> list[BaseChatModel]:
    """Lista ordenada de modelos LangChain + stub no fim (cascata de fallback)."""
    cfgs, stub_model = provider_configs_for_agent(agent_name)
    label = default_llm_run_name(agent_name) if agent_name else None
    models = [m for m in (build_chat_model(c, llm_run_name=label) for c in cfgs) if m is not None]
    models.append(make_stub_chat_model(stub_model, name=label))
    return models


def get_chat_model(agent_name: str = "") -> BaseChatModel:
    """Choke point: 1º modelo real da cascata resolvida, ou stub."""
    models = resolve_chat_models(agent_name)
    return models[0] if models else make_stub_chat_model()


class OutputOverrideChatModel(BaseChatModel):
    """Substitui o texto final do LLM antes dos callbacks (workshop UC-3 Correctness span)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: Any = Field(exclude=True)
    override_text: str = Field(exclude=True)
    run_name: str | None = Field(default=None, exclude=True)

    @property
    def _llm_type(self) -> str:
        return getattr(self.inner, "_llm_type", "output-override")

    def _replace_generations(self, result):
        from langchain_core.outputs import ChatGeneration

        gens = []
        for gen in result.generations:
            msg = gen.message
            if isinstance(msg, AIMessage):
                gens.append(ChatGeneration(
                    message=AIMessage(
                        content=self.override_text,
                        usage_metadata=getattr(msg, "usage_metadata", None),
                        response_metadata=getattr(msg, "response_metadata", None),
                        tool_calls=getattr(msg, "tool_calls", None) or [],
                    ),
                    text=self.override_text,
                ))
            else:
                gens.append(gen)
        result.generations = gens
        return result

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = self.inner._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        return self._replace_generations(result)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        result = await self.inner._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        return self._replace_generations(result)

    def _wrap_bound(self, bound: Any) -> "OutputOverrideChatModel":
        wrapped = OutputOverrideChatModel(
            inner=bound,
            override_text=self.override_text,
            run_name=self.run_name,
        )
        if self.run_name:
            wrapped = wrapped.with_config({"run_name": self.run_name, "name": self.run_name})
        return wrapped

    def bind(self, **kwargs):
        return self._wrap_bound(self.inner.bind(**kwargs))

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self._wrap_bound(self.inner.bind_tools(tools, **kwargs))


def wrap_llm_output(
    model: BaseChatModel,
    override_text: str,
    *,
    run_name: str | None = None,
) -> BaseChatModel:
    wrapped = OutputOverrideChatModel(
        inner=model,
        override_text=override_text,
        run_name=run_name,
    )
    if run_name:
        wrapped = wrapped.with_config({"run_name": run_name, "name": run_name})
    return wrapped


def _model_identity(model: BaseChatModel) -> tuple[str, str, str]:
    if isinstance(model, OutputOverrideChatModel):
        return _model_identity(model.inner)
    if isinstance(model, VegaStubChatModel):
        return model.provider, model.family, model.model_name
    provider = getattr(model, "_vega_provider", None) or getattr(model, "model_name", "unknown")
    family = getattr(model, "_vega_family", None) or "openai"
    model_name = getattr(model, "model_name", None) or getattr(model, "model", DEFAULT_STUB_MODEL)
    return str(provider), str(family), str(model_name)


def _prompt_cache_tokens_from_maps(usage: dict, meta: dict, token_usage: dict) -> int:
    """Extrai tokens de prompt-cache do provider de usage_metadata / response_metadata."""
    for src in (usage, token_usage, meta):
        if not isinstance(src, dict):
            continue
        v = src.get("cache_read_input_tokens") or src.get("cache_read")
        if v:
            return int(v)
        itd = src.get("input_token_details")
        if isinstance(itd, dict):
            v = itd.get("cache_read") or itd.get("cache_read_input_tokens")
            if v:
                return int(v)
        details = src.get("prompt_tokens_details")
        if isinstance(details, dict) and details.get("cached_tokens"):
            return int(details["cached_tokens"])
        if details is not None and not isinstance(details, dict):
            v = getattr(details, "cached_tokens", 0) or 0
            if v:
                return int(v)
    return 0


def _extract_usage(response: AIMessage) -> tuple[int, int, str, int]:
    """Devolve (input_tokens, output_tokens, model, prompt_cache_tokens).

    `prompt_cache_tokens` = tokens de input cobertos por prompt-cache do provider
    (OpenAI `cached_tokens`, Anthropic `cache_read_*`) — F-COST-CACHE; 0 se ausente."""
    usage = response.usage_metadata or {}
    if not isinstance(usage, dict):
        usage = {}
    in_tok = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    meta = response.response_metadata or {}
    if not isinstance(meta, dict):
        meta = {}
    model = str(meta.get("model") or meta.get("model_name") or "")
    token_usage = meta.get("token_usage") or meta.get("usage") or {}
    if not isinstance(token_usage, dict):
        token_usage = {}
    if not in_tok and not out_tok:
        in_tok = int(token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0)
        out_tok = int(token_usage.get("completion_tokens") or token_usage.get("output_tokens") or 0)
    cache_tok = _prompt_cache_tokens_from_maps(usage, meta, token_usage)
    return in_tok, out_tok, model, cache_tok


def _with_run_name(bound: Any, model: BaseChatModel, run_name: str | None = None) -> Any:
    """Mitiga perda de `name` após `bind` / `bind_tools` — padrão golden-demo."""
    label = run_name or getattr(model, "name", None)
    if label:
        return bound.with_config({"run_name": label, "name": label})
    return bound


def invoke_to_llm_result(
    model: BaseChatModel,
    system: str,
    prompt: str,
    *,
    verbose: bool = False,
    max_tokens: int | None = None,
    fallback: bool = False,
    config=None,
    run_name: str | None = None,
) -> LLMResult:
    """Chama `model.invoke` com System+Human messages e devolve `LLMResult` coerente."""
    messages = [make_system_message(model, system), HumanMessage(content=prompt)]
    token_limit = max_tokens if max_tokens is not None else (512 if verbose else 256)
    bound: Any = model
    if not isinstance(model, VegaStubChatModel):
        bound = model.bind(max_tokens=token_limit)
    else:
        bound = model.bind(verbose=verbose, max_tokens=max_tokens)
    bound = _with_run_name(bound, model, run_name)
    response = bound.invoke(messages, config=config)
    if not isinstance(response, AIMessage):
        response = AIMessage(content=str(getattr(response, "content", response)))
    text = response.content if isinstance(response.content, str) else str(response.content)
    in_tok, out_tok, resp_model, cache_tok = _extract_usage(response)
    provider, family, default_model = _model_identity(model)
    return LLMResult(
        text,
        in_tok,
        out_tok,
        resp_model or default_model,
        provider=provider,
        system=family,
        fallback=fallback,
        prompt_cache_tokens=cache_tok,
    )


def invoke_chat_cascade(
    agent_name: str,
    system: str,
    prompt: str,
    *,
    run_name: str,
    max_tokens: int | None = None,
    verbose: bool = False,
    config=None,
) -> LLMResult:
    """Cascata observável: ChatOpenAI/Anthropic/Bedrock → stub, com nome real do modelo no span."""
    models = resolve_chat_models(agent_name)
    errors: list[CascadeError] = []
    last_err: Exception | None = None

    for i, candidate in enumerate(models):
        if i == 0:
            candidate = get_chat_model(agent_name)
        is_last = i >= len(models) - 1
        if not llm_rate_allow():
            stub = make_stub_chat_model(settings.llm_stub_model)
            return invoke_to_llm_result(
                stub,
                system,
                prompt,
                max_tokens=max_tokens,
                verbose=verbose,
                config=config,
                run_name=run_name,
            )
        try:
            result = invoke_to_llm_result(
                candidate,
                system,
                prompt,
                max_tokens=max_tokens,
                verbose=verbose,
                config=config,
                run_name=run_name,
                fallback=i > 0,
            )
            if errors and is_stub_output(result.text) and not is_last:
                continue
            text = apply_cascade_stub_policy(result.text, errors)
            if text != result.text:
                result = LLMResult(
                    text,
                    result.input_tokens,
                    result.output_tokens,
                    result.model,
                    provider=result.provider,
                    system=result.system,
                    fallback=result.fallback,
                    prompt_cache_tokens=result.prompt_cache_tokens,
                )
            return result
        except Exception as exc:  # noqa: BLE001 — tenta o próximo provider da cascata
            last_err = exc
            _record_cascade_error(errors, candidate, exc)
            continue

    if errors:
        provider, family, model_key = errors[0]
        return LLMResult(
            format_llm_provider_error(errors),
            0,
            0,
            model_key,
            provider=provider,
            system=family,
        )
    raise RuntimeError(f"chat cascade failed: {type(last_err).__name__ if last_err else 'empty'}")
