"""LangChain chains para features de IA avulsas (F-OBS-PREP-6).

Cada feature (`product_qa`, `comparator`, etc.) usa `ChatPromptTemplate | get_chat_model`.
Cache/rate-limit ficam no boundary (`llm_cache.invoke_cached`), não dentro do modelo.

F-GALILEO-17: retrieval RAG (políticas/catálogo) e LLM num único `chain.invoke` — retriever
span L4r aninhado sob `feature.{step}`, sem traces órfãos no Console Splunk Agent Observability.
"""
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda, RunnableWithFallbacks
from langchain_core.runnables.config import RunnableConfig

from . import agent_config
from . import rag
from .galileo_span import (
    BUSINESS_STEPS,
    MERGE_CATALOG_CONTEXT_RUN_NAME,
    MERGE_CATALOG_RETRIEVE_RUN_NAME,
    MERGE_POLICY_CONTEXT_RUN_NAME,
    MERGE_POLICY_RETRIEVE_RUN_NAME,
    MERGE_STATIC_CONTEXT_RUN_NAME,
    default_llm_run_name,
    llm_run_name,
)
from .llm import LLMResult
from .llm_cache import build_cache_miss_chain, invoke_cached_chain
from .llm_models import (
    VegaStubChatModel,
    _extract_usage,
    _model_identity,
    format_llm_provider_error,
    get_chat_model,
    invoke_to_llm_result,
    is_stub_output,
    make_stub_chat_model,
    make_system_message,
    provider_prompt_cache_enabled,
    resolve_chat_models,
    _with_run_name,
)


def _token_limit(*, max_tokens: int | None, verbose: bool) -> int | None:
    if max_tokens is not None:
        return max_tokens
    return 512 if verbose else 256


def _response_to_llm_result(model, response, *, fallback: bool = False) -> LLMResult:
    if not isinstance(response, AIMessage):
        response = AIMessage(content=str(getattr(response, "content", response)))
    text = response.content if isinstance(response.content, str) else str(response.content)
    in_tok, out_tok, resp_model, cache_tok = _extract_usage(response)
    provider, family, default_model = _model_identity(model)
    return LLMResult(
        text, in_tok, out_tok, resp_model or default_model,
        provider=provider, system=family, fallback=fallback,
        prompt_cache_tokens=cache_tok,
    )


def _feature_run_name(feature: str) -> str:
    step = BUSINESS_STEPS.get(feature, feature.replace("-", "_"))
    return llm_run_name("feature", step)


def _named_lambda(fn, run_name: str) -> RunnableLambda:
    """LangChain/Splunk Agent Observability usam `name` no callback — `run_name` sozinho deixa RunnableLambda genérico."""
    return RunnableLambda(fn, name=run_name).with_config({"run_name": run_name, "name": run_name})


def _named_chain(first: Runnable, second: Runnable, run_name: str) -> Runnable:
    return (first | second).with_config({"run_name": run_name, "name": run_name})


def _chain_for_model(feature: str, model, system: str, *,
                     max_tokens: int | None = None, verbose: bool = False) -> Runnable:
    token_limit = _token_limit(max_tokens=max_tokens, verbose=verbose)
    bound: Any = model
    if not isinstance(model, VegaStubChatModel):
        bound = model.bind(max_tokens=token_limit)
    else:
        bound = model.bind(verbose=verbose, max_tokens=max_tokens)
    run_name = _feature_run_name(feature)
    bound = _with_run_name(bound, model, run_name)
    if provider_prompt_cache_enabled() and _model_identity(model)[1] == "anthropic":
        sys_msg = make_system_message(model, system)

        def _msgs(x: dict) -> list:
            return [sys_msg, HumanMessage(content=x["input"])]

        prepare_name = f"{run_name}.prepare_messages"
        prepare = _named_lambda(_msgs, prepare_name)
        return _named_chain(prepare, bound.with_config({"run_name": run_name, "name": run_name}), run_name)
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content=system),
        ("human", "{input}"),
    ])
    return (prompt | bound).with_config({"run_name": run_name, "name": run_name})


def _chain_for_dynamic_system(feature: str, model, *,
                              max_tokens: int | None = None, verbose: bool = False) -> Runnable:
    """Chain com system montado em runtime (`system_context` no state) — pós-retrieval RAG."""
    token_limit = _token_limit(max_tokens=max_tokens, verbose=verbose)
    bound: Any = model
    if not isinstance(model, VegaStubChatModel):
        bound = model.bind(max_tokens=token_limit)
    else:
        bound = model.bind(verbose=verbose, max_tokens=max_tokens)
    run_name = _feature_run_name(feature)
    bound = _with_run_name(bound, model, run_name)

    def _messages(state: dict) -> list:
        system = _effective_system(feature, context=(state.get("system_context") or "").strip())
        return [SystemMessage(content=system), HumanMessage(content=state["input"])]

    prepare_name = f"{run_name}.prepare_messages"
    prepare = _named_lambda(_messages, prepare_name)
    return _named_chain(prepare, bound.with_config({"run_name": run_name, "name": run_name}), run_name)


def _postprocess_cascade_stub_response(response, feature: str) -> AIMessage:
    """Stub no fim da cascata após provider real → mensagem legível (sem RunnableLambda extra)."""
    if not isinstance(response, AIMessage):
        response = AIMessage(content=str(getattr(response, "content", response)))
    text = response.content if isinstance(response.content, str) else str(response.content)
    if not is_stub_output(text):
        return response
    models = resolve_chat_models(feature)
    if not models or isinstance(models[0], VegaStubChatModel):
        return response
    provider, family, model_key = _model_identity(models[0])
    msg = format_llm_provider_error([
        (provider, family, model_key, "primary provider failed; cascade fell back to offline stub"),
    ])
    return AIMessage(content=msg)


def _build_llm_cascade_runnable(feature: str, *, dynamic_system: bool,
                                max_tokens: int | None, verbose: bool) -> Runnable:
    """Cascata de modelos como LCEL — LLM span aninhado sob a feature chain."""
    from .llm_cache import response_cache_invoke_run_name

    run_name = _feature_run_name(feature)
    invoke_name = response_cache_invoke_run_name(run_name)
    models = resolve_chat_models(feature)
    chains: list[Runnable] = []
    for i, _model in enumerate(models):
        model = get_chat_model(feature) if i == 0 else _model
        if dynamic_system:
            chains.append(_chain_for_dynamic_system(
                feature, model, max_tokens=max_tokens, verbose=verbose,
            ))
        else:
            cfg = agent_config.get_agent(feature)
            system = agent_config.effective_system(cfg)
            chains.append(_chain_for_model(
                feature, model, system, max_tokens=max_tokens, verbose=verbose,
            ))
    primary = chains[0]
    fallbacks = chains[1:]
    llm = RunnableWithFallbacks(runnable=primary, fallbacks=fallbacks) if fallbacks else primary
    return llm.with_config({"run_name": invoke_name, "name": invoke_name})


def _merge_static_context(state: dict) -> dict:
    parts = [state.get("static_context") or "", state.get("context_suffix") or ""]
    return {
        "input": state["input"],
        "system_context": "\n\n".join(p.strip() for p in parts if p and p.strip()).strip(),
    }


def _merge_policy_context(state: dict) -> dict:
    policy_block = rag.format_policy_documents(state.get("policies") or [])
    parts = [state.get("static_context") or "", policy_block, state.get("context_suffix") or ""]
    return {
        "input": state["input"],
        "system_context": "\n\n".join(p.strip() for p in parts if p and p.strip()).strip(),
    }


def _merge_catalog_context(state: dict) -> dict:
    from .ai_features import catalog_index_from_documents

    catalog_block = catalog_index_from_documents(state.get("catalog_docs") or [])
    suffix = state.get("context_suffix") or ""
    system_context = f"Catalog (sku: name [tags] price, most relevant first):\n{catalog_block}\n\n{suffix}".strip()
    return {"input": state["input"], "system_context": system_context}


def _merge_combined_context(state: dict, *, catalog_mode: str) -> dict:
    """Merge pós-retrieval — suporta catálogo (índice ou chunks completos) + políticas."""
    parts = [state.get("static_context") or ""]
    catalog_docs = state.get("catalog_docs") or []
    if catalog_docs:
        if catalog_mode == "full":
            parts.append(rag.format_catalog_documents(catalog_docs))
        else:
            from .ai_features import catalog_index_from_documents

            block = catalog_index_from_documents(catalog_docs)
            parts.append(f"Catalog (sku: name [tags] price, most relevant first):\n{block}")
    policies = state.get("policies") or []
    if policies:
        parts.append(rag.format_policy_documents(policies))
    parts.append(state.get("context_suffix") or "")
    return {
        "input": state["input"],
        "system_context": "\n\n".join(p.strip() for p in parts if p and p.strip()).strip(),
    }


def _build_prep_runnable(
    *,
    policy_retrieval: bool,
    catalog_retrieval: bool,
    catalog_mode: str = "index",
    policy_k: int | None = None,
    catalog_k: int | None = None,
) -> Runnable | None:
    retrieve_steps: list[Runnable] = []

    if catalog_retrieval:
        k = catalog_k or (3 if catalog_mode == "full" else 20)

        def _retrieve_catalog(state: dict, config: RunnableConfig | None = None) -> dict:
            query = state["input"]
            name = (state.get("product_name") or "").strip()
            if name:
                query = f"{name} {query}".strip()
            docs = rag.catalog_retriever_runnable(k=k).invoke(query, config=config)
            sku = state.get("product_sku")
            if sku and catalog_mode == "full":
                sku_docs = [d for d in docs if d.metadata.get("sku") == sku]
                rest = [d for d in docs if d.metadata.get("sku") != sku]
                docs = (sku_docs + rest)[:k]
            return {**state, "catalog_docs": docs}

        retrieve_steps.append(
            _named_lambda(_retrieve_catalog, MERGE_CATALOG_RETRIEVE_RUN_NAME)
        )

    if policy_retrieval:
        k = policy_k if policy_k is not None else rag.DEFAULT_K

        def _retrieve_policies(state: dict, config: RunnableConfig | None = None) -> dict:
            policies = rag.policy_retriever_runnable(k=k).invoke(state["input"], config=config)
            return {**state, "policies": policies}

        retrieve_steps.append(
            _named_lambda(_retrieve_policies, MERGE_POLICY_RETRIEVE_RUN_NAME)
        )

    if not retrieve_steps:
        return _named_lambda(_merge_static_context, MERGE_STATIC_CONTEXT_RUN_NAME)

    merge_name = (
        MERGE_POLICY_CONTEXT_RUN_NAME
        if policy_retrieval and catalog_retrieval
        else MERGE_CATALOG_CONTEXT_RUN_NAME
        if catalog_retrieval
        else MERGE_POLICY_CONTEXT_RUN_NAME
    )

    def _merge(state: dict) -> dict:
        return _merge_combined_context(state, catalog_mode=catalog_mode)

    merge = _named_lambda(_merge, merge_name)
    prep: Runnable = retrieve_steps[0]
    for step in retrieve_steps[1:]:
        prep = prep | step
    return _named_chain(prep, merge, merge_name)


def build_feature_chain(feature: str, *, max_tokens: int | None = None,
                        verbose: bool = False) -> tuple[Runnable, str, str, Any]:
    """Monta chain LCEL do modelo primário. Devolve (chain, system, model_key, model)."""
    cfg = agent_config.get_agent(feature)
    system = agent_config.effective_system(cfg)
    model = get_chat_model(feature)
    _, _, model_key = _model_identity(model)
    chain = _chain_for_model(feature, model, system, max_tokens=max_tokens, verbose=verbose)
    return chain, system, model_key, model


def _effective_system(feature: str, *, context: str = "") -> str:
    cfg = agent_config.get_agent(feature)
    system = agent_config.effective_system(cfg)
    ctx = (context or "").strip()
    if ctx:
        system = f"{system}\n\n{ctx}".strip()
    return system


def invoke_feature_chain(feature: str, user_prompt: str, *,
                         max_tokens: int | None = None, verbose: bool = False,
                         use_cache: bool = True, config=None,
                         context: str = "",
                         static_context: str = "",
                         context_suffix: str = "",
                         policy_retrieval: bool = False,
                         catalog_retrieval: bool = False,
                         catalog_mode: str = "index",
                         policy_k: int | None = None,
                         catalog_k: int | None = None,
                         product_sku: str = "",
                         product_name: str = ""):
    """Invoca a chain da feature com cache F-022. Devolve `(LLMResult, status)`.

    `user_prompt` vira a mensagem human do LLM. Contexto estático + sufixo entram no system;
    com `policy_retrieval`/`catalog_retrieval`, o retriever roda **dentro** da mesma chain
    (span L4r aninhado — ADR-031 / padrão healthcare).

    `context` (legado): se passado, equivale a `static_context` + `context_suffix` combinados."""
    legacy = (context or "").strip()
    if legacy and not (static_context or context_suffix):
        static_context, context_suffix = legacy, ""
    elif legacy:
        static_context = f"{static_context}\n\n{legacy}".strip() if static_context else legacy

    uses_rag = policy_retrieval or catalog_retrieval
    if uses_rag:
        system_for_key = _effective_system(
            feature,
            context="\n\n".join(
                p for p in (static_context, context_suffix) if p and p.strip()
            ).strip(),
        )
    else:
        system_for_key = _effective_system(
            feature,
            context="\n\n".join(
                p for p in (static_context, context_suffix) if p and p.strip()
            ).strip(),
        )

    model = get_chat_model(feature)
    _, _, model_key = _model_identity(model)

    prep = _build_prep_runnable(
        policy_retrieval=policy_retrieval,
        catalog_retrieval=catalog_retrieval,
        catalog_mode=catalog_mode,
        policy_k=policy_k,
        catalog_k=catalog_k,
    )
    llm_runnable = _build_llm_cascade_runnable(
        feature, dynamic_system=uses_rag or bool(static_context or context_suffix),
        max_tokens=max_tokens, verbose=verbose,
    )
    miss_chain = build_cache_miss_chain(feature, llm_runnable, prep=prep)
    miss_input = {
        "input": user_prompt,
        "static_context": static_context,
        "context_suffix": context_suffix,
        "product_sku": product_sku,
        "product_name": product_name,
    }

    def to_llm_result(response) -> LLMResult:
        response = _postprocess_cascade_stub_response(response, feature)
        return _response_to_llm_result(model, response)

    def degrade_fn():
        return invoke_to_llm_result(
            make_stub_chat_model(model_key, name=default_llm_run_name(feature)),
            system_for_key, user_prompt,
            verbose=verbose, max_tokens=max_tokens,
        )

    return invoke_cached_chain(
        feature, user_prompt, model_key, miss_chain, miss_input,
        to_llm_result=to_llm_result,
        system=system_for_key, max_tokens=max_tokens, verbose=verbose,
        degrade_fn=degrade_fn, use_cache=use_cache, config=config,
    )
