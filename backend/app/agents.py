"""Orquestração AI do Vega (LangGraph hub-and-spoke + features) — F-025 → F-050 / ADR-029.

**Concierge:** hub-and-spoke (`coordinator` → curator/respond → finalize). **Compare /
fulfillment / returns:** grafos ReAct (`agent` + `ToolNode` + `finalize`). Ops de negócio são
**StructuredTools**; decisões com toggle workshop (fraude, eligibility, abuse) e o comparator
ficam no **finalize** (código + LLM avulso). Fallback determinístico no stub.

**Features de loja:** `feature_complete` → LCEL (`feature_chains`) + cache F-022.
"""
import contextvars
import json
import re
import time
from typing import TypedDict, List, Optional
from langchain_core.messages import AIMessage, HumanMessage
from .graphs.concierge import build_concierge_graph
from .graphs.chat import build_chat_graph
from .runnable_config import resolve_config, set_current_runnable_config
from .llm import get_llm, LLMResult
from .llm_providers import current_provider_cfgs as _current_provider_cfgs, load_provider_configs
from . import agent_config, llm_activity
from .llm_models import (
    CascadeError,
    _record_cascade_error,
    apply_cascade_stub_policy,
    format_llm_provider_error,
    get_chat_model,
    invoke_to_llm_result,
    is_stub_output,
    resolve_chat_models,
)
from .feature_chains import invoke_feature_chain
from .galileo_span import agent_llm_run_name
from . import galileo_control
from .problems import FLAGS
from .tools import CATALOG, REFUND_WINDOW_DAYS

_CATALOG_MAX_PRICE = max(p["price"] for p in CATALOG)


def parse_budget_from_text(text: str) -> float | None:
    """Extrai orçamento do texto do shopper (R$/$/até/under). None se não mencionado."""
    if not text:
        return None
    for pat in (
        r"(?:até|ate|under|below|max(?:imum)?|budget)\s*(?:r?\$?\s*)?([\d.]+)",
        r"r?\$\s*([\d.]+)",
        r"Budget:\s*R?\$?([\d.]+)",
    ):
        m = re.search(pat, text, re.I)
        if m:
            return float(m.group(1))
    return None


def resolve_budget(text: str) -> float:
    """Orçamento p/ filtro de catálogo: do texto ou teto do catálogo (sem default fixo 300)."""
    return parse_budget_from_text(text) or _CATALOG_MAX_PRICE

# LLM resolvido UMA vez por execução (run_workflow) a partir da config corrente — não
# fixado no import (F-020). Os nós leem a cascata daqui; fora de um run (ex.: run_demo
# chama build_graph direto) cada nó resolve sob demanda via get_llm().
_current_llm: contextvars.ContextVar = contextvars.ContextVar("current_llm", default=None)

class OrderState(TypedDict, total=False):
    request: str; constraints: dict; candidates: List[dict]
    selected: Optional[dict]; answer: str; language: str
    inventory_status: dict; quote: dict; fraud_result: dict; order: dict
    messages: List[str]; quality: dict


def _run_agent_llm(agent_name: str, prompt: str, verbose: bool = False, *,
                   config=None, workflow: str | None = None, response_override: str | None = None):
    """Roda o LLM de um agente (config por-agente F-021) e registra no Inspector local. Devolve o
    LLMResult. Reusado por `agent_turn`/`call_agent`. Caminho LangChain (F-OBS-PREP-1/7)."""
    cfg = agent_config.get_agent(agent_name)
    system = agent_config.effective_system(cfg)
    resolved = resolve_config(config, feature=agent_name)
    run_name = agent_llm_run_name(workflow, agent_name) if workflow else None
    t0 = time.perf_counter()
    models = resolve_chat_models(agent_name)
    last_err: Exception | None = None
    errors: list[CascadeError] = []
    r = None
    for i, model in enumerate(models):
        if i == 0:
            model = get_chat_model(agent_name)
        if response_override is not None:
            from .llm_models import wrap_llm_output
            model = wrap_llm_output(model, response_override)
        try:
            r = invoke_to_llm_result(
                model, system, prompt, verbose=verbose, fallback=i > 0, config=resolved,
                run_name=run_name,
            )
            text = apply_cascade_stub_policy(r.text, errors)
            if text != r.text:
                r = LLMResult(
                    text, r.input_tokens, r.output_tokens, r.model,
                    provider=r.provider, system=r.system, fallback=True,
                    prompt_cache_tokens=getattr(r, "prompt_cache_tokens", 0) or 0,
                )
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            _record_cascade_error(errors, model, e)
            continue
    if r is None:
        if errors:
            msg = format_llm_provider_error(errors)
            provider, family, model_key, _ = errors[0]
            r = LLMResult(msg, 0, 0, model_key, provider=provider, system=family, fallback=True)
        else:
            raise RuntimeError(f"todos os modelos da cascata falharam: {type(last_err).__name__}")
    latency_ms = (time.perf_counter() - t0) * 1000
    llm_activity.record(
        feature=agent_name, system=system, prompt=prompt, response=r.text,
        model=r.model, provider=r.provider, family=r.system,
        input_tokens=r.input_tokens, output_tokens=r.output_tokens,
        cache=None, latency_ms=latency_ms, fallback=r.fallback,
        prompt_cache_tokens=getattr(r, "prompt_cache_tokens", 0) or 0)
    return r


def agent_turn(agent_name: str, prompt: str, verbose=False, *, config=None, workflow: str | None = None):
    """Turno de raciocínio de um agente (LLM leaf/coordinator helper). Devolve LLMResult."""
    return _run_agent_llm(agent_name, prompt, verbose, config=config, workflow=workflow)


def call_agent(agent_name: str, prompt: str, *, verbose=False, config=None,
               workflow: str | None = None) -> str:
    """Sub-agente FOLHA: faz UMA chamada de LLM e devolve o texto."""
    return _run_agent_llm(agent_name, prompt, verbose, config=config, workflow=workflow).text


def _feature_invoke(
    feature: str,
    user_turn: str,
    *,
    context: str = "",
    static_context: str = "",
    context_suffix: str = "",
    policy_retrieval: bool = False,
    catalog_retrieval: bool = False,
    catalog_mode: str = "index",
    policy_k: int | None = None,
    catalog_k: int | None = None,
    product_sku: str = "",
    product_name: str = "",
    max_tokens: int | None = None,
    verbose: bool = False,
    use_cache: bool = True,
    config=None,
    control_fallback=None,
) -> tuple[str, object, str]:
    """Núcleo de feature_complete / feature_complete_turn — human = user_turn, context no system."""
    cfg = agent_config.get_agent(feature)
    ctx_parts = [
        (context or "").strip(),
        (static_context or "").strip(),
        (context_suffix or "").strip(),
    ]
    merged_ctx = "\n\n".join(p for p in ctx_parts if p).strip()
    system = agent_config.effective_system(cfg)
    if merged_ctx:
        system = f"{system}\n\n{merged_ctx}".strip()
    resolved = resolve_config(config, feature=feature)
    t0 = time.perf_counter()

    def _invoke_chain(user_prompt: str = user_turn):
        return invoke_feature_chain(
            feature, user_prompt, max_tokens=max_tokens, verbose=verbose,
            use_cache=use_cache, config=resolved, context=context,
            static_context=static_context, context_suffix=context_suffix,
            policy_retrieval=policy_retrieval, catalog_retrieval=catalog_retrieval,
            catalog_mode=catalog_mode, policy_k=policy_k, catalog_k=catalog_k,
            product_sku=product_sku, product_name=product_name,
        )

    if galileo_control.is_active() and feature in galileo_control.CONTROLLED_FEATURES:
        text, r, status = galileo_control.controlled_feature_invoke(
            feature, user_turn, lambda: _invoke_chain(user_turn),
            chain_invoke=_invoke_chain,
            control_fallback=control_fallback,
        )
    else:
        r, status = _invoke_chain(user_turn)
        text = r.text
    latency_ms = (time.perf_counter() - t0) * 1000
    llm_activity.record(
        feature=feature, system=system, prompt=user_turn, response=r.text,
        model=r.model, provider=r.provider, family=r.system,
        input_tokens=r.input_tokens, output_tokens=r.output_tokens,
        cache=status, latency_ms=latency_ms, fallback=r.fallback,
        prompt_cache_tokens=getattr(r, "prompt_cache_tokens", 0) or 0)
    return text, r, status


def feature_complete(feature: str, prompt: str, *, max_tokens: int | None = None,
                     verbose: bool = False, use_cache: bool = True, config=None,
                     control_fallback=None):
    """Chamada de IA de UMA feature de produto/loja (F-022) — Q&A, descrição, busca etc. Cada
    feature é um "agente" configurável (reusa a config por-agente da F-021). Aplica a camada de
    controle de custo (cache + single-flight + rate-limit + `max_tokens`) via LangChain chain +
    `llm_cache.invoke_cached`. Devolve `(texto, LLMResult, status)`.

    `config` (RunnableConfig) propaga callbacks/metadata; se omitido, usa contextvar ou default."""
    return _feature_invoke(
        feature, prompt,
        max_tokens=max_tokens, verbose=verbose, use_cache=use_cache,
        config=config, control_fallback=control_fallback,
    )


def feature_complete_turn(feature: str, user_turn: str, *, context: str = "",
                          static_context: str = "", context_suffix: str = "",
                          policy_retrieval: bool = False, catalog_retrieval: bool = False,
                          catalog_mode: str = "index",
                          policy_k: int | None = None, catalog_k: int | None = None,
                          product_sku: str = "", product_name: str = "",
                          max_tokens: int | None = None, verbose: bool = False,
                          use_cache: bool = True, config=None,
                          control_fallback=None):
    """Como `feature_complete`, mas separa turno do comprador (human) do contexto (system).

    Melhora o input no trace Splunk Agent Observability (evaluators Prompt Injection, Context Adherence, etc.).
    Com `policy_retrieval`/`catalog_retrieval`, o retriever roda dentro da mesma chain LCEL."""
    return _feature_invoke(
        feature, (user_turn or "").strip(), context=context,
        static_context=static_context, context_suffix=context_suffix,
        policy_retrieval=policy_retrieval, catalog_retrieval=catalog_retrieval,
        catalog_mode=catalog_mode, policy_k=policy_k, catalog_k=catalog_k,
        product_sku=product_sku, product_name=product_name,
        max_tokens=max_tokens, verbose=verbose, use_cache=use_cache,
        config=config, control_fallback=control_fallback,
    )


# --- Parsing tolerante de decisão (structured-output + fallback) -------------

_JSON_FENCE_SUFFIX = " Reply with raw JSON only — no markdown code fences."


def strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences (```json / ```) wrapping LLM output."""
    if not text:
        return text
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _parse_json(text: str) -> dict | None:
    """Extrai o 1º objeto JSON do texto do LLM (tolera cercas ```json e texto ao redor).
    Devolve None se não há JSON parseável (ex.: stub offline) → o chamador cai p/ o
    fallback determinístico."""
    if not text:
        return None
    text = strip_markdown_fences(text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, dict) else None
    except (ValueError, TypeError):
        return None


_PT_HINTS = ("ç", "ã", "õ", "á", "é", "í", "ó", "ú", "â", "ê")
_PT_WORDS = ("presente", "aniversário", "até", "para", "que", "com", "preço", "barato")


def _detect_language(text: str) -> str:
    """Heurística leve de idioma do pedido (controle de idioma da resposta — F-025). Sem libs
    (orçamento 2vCPU/4GB): acentos/keywords PT → 'pt'; senão 'en'. O fallback do stub usa isto."""
    low = (text or "").lower()
    if any(h in low for h in _PT_HINTS) or any(w in low.split() for w in _PT_WORDS):
        return "pt"
    return "en"


_CATEGORY_KEYS = {
    "audio": ("audio", "fone", "headphone", "som", "speaker", "earbud", "soundbar"),
    "wearable": ("wearable", "watch", "smartwatch", "band", "anel", "ring"),
    "casa": ("casa", "home", "café", "coffee", "luminária", "lamp", "garrafa", "umidificador"),
    "presente": ("presente", "gift", "aniversário", "birthday"),
}


def _extract_constraints_fallback(request: str, budget: float) -> dict:
    """Restrições determinísticas a partir do texto (fallback do supervisor quando o LLM não
    devolve JSON — stub/modelo fraco). Mesma forma do contrato structured-output."""
    low = (request or "").lower()
    category = next((cat for cat, keys in _CATEGORY_KEYS.items()
                     if cat != "presente" and any(k in low for k in keys)), "")
    occasion = "gift" if any(k in low for k in _CATEGORY_KEYS["presente"]) else ""
    return {"budget": budget, "category": category, "occasion": occasion,
            "language": _detect_language(request)}


# --- Helpers de curadoria / resposta (finalize concierge + stub) ------------

def _pick_selected(candidates: list[dict], constraints: dict, decision: dict | None) -> dict | None:
    """Escolhe SKU a partir de decisão opcional (JSON) ou cura determinística por categoria/preço."""
    if not candidates:
        return None
    if decision:
        sku = decision.get("selected_sku")
        hit = next((c for c in candidates if c["sku"] == sku), None)
        if hit:
            return hit
    cat = (constraints or {}).get("category") or ""
    pool = [c for c in candidates if cat and cat in c["tags"]] or candidates
    return sorted(pool, key=lambda c: c["price"])[len(pool) // 2 if len(pool) > 2 else 0]


def _compose_response(request: str, selected: dict | None, constraints: dict, *, config=None) -> str:
    """Resposta grounded bilíngue via agente respond, com fallback template."""
    lang = (constraints or {}).get("language") or _detect_language(request)
    if not selected:
        return _fallback_response(None, lang)
    price = selected.get("quote", {}).get("price", selected.get("price"))
    facts = (
        f"Product: {selected.get('name')} (SKU {selected.get('sku')})\n"
        f"Price: ${price:.0f}\n"
        f"Shopper request: {request}\n"
        f"Reply in English."
    )
    text = call_agent("respond", facts, verbose=False, config=config, workflow="concierge")
    if not text or is_stub_output(text):
        return _fallback_response(selected, lang)
    return text


def _fallback_response(selected: dict | None, lang: str) -> str:
    """Deterministic grounded reply (standalone/stub) — en-US storefront."""
    if not selected:
        return "We couldn't find an ideal match. Try widening your budget or search."
    price = selected.get("quote", {}).get("price", selected["price"])
    return f"We recommend the {selected['name']} at ${price:.0f} — a great fit for what you asked."


# --- Fechamento orquestrado (Fulfillment Coordinator → fraude → tools) -------

def _order_days_since_delivery(order: dict) -> float | None:
    from datetime import datetime, timezone

    for h in order.get("history", []):
        if h.get("status") == "DELIVERED":
            try:
                at = datetime.fromisoformat(h["at"])
                return (datetime.now(timezone.utc) - at).total_seconds() / 86400.0
            except (ValueError, TypeError):
                return None
    return None


def refund_eligibility(order: dict, *, config=None, apply_workshop_toggles: bool = True) -> dict:
    """Agente de ELEGIBILIDADE (LLM no Inspector). Decisão efetiva é **workshop-safe** (F-OBS-PREP-7):

    - `refund_false_denial` → **not eligible** (erro de decisão sobre dado correto).
    - senão → elegibilidade por dados (status DELIVERED + janela).

    O LLM roda e o JSON dele fica em `llm_eligible`/`llm_response`; a decisão aplicada
    está em `eligible`/`source` (Python + toggle).
    """
    days = _order_days_since_delivery(order)
    eligible_data = (
        order.get("status") == "DELIVERED"
        and days is not None
        and days <= REFUND_WINDOW_DAYS
    )
    prompt = (
        f"Order {order['id']} — status {order['status']}, "
        f"delivered {f'{days:.1f} days ago' if days is not None else 'not delivered'}. "
        f"Return window is {REFUND_WINDOW_DAYS} days. Is it eligible for a refund? "
        'Reply ONLY with JSON {"eligible": true|false, "reason": "<short>"}.'
        + _JSON_FENCE_SUFFIX
    )
    false_denial = apply_workshop_toggles and FLAGS.refund_false_denial and eligible_data
    response_override = None
    if false_denial:
        wrong_days = int((days or 0) + REFUND_WINDOW_DAYS + 15)
        response_override = json.dumps({
            "eligible": False,
            "reason": (
                f"Delivered {wrong_days} days ago — outside the {REFUND_WINDOW_DAYS}-day window."
            ),
        })
    r = _run_agent_llm(
        "eligibility", prompt, verbose=FLAGS.cost_spike, config=config, workflow="returns",
        response_override=response_override,
    )
    parsed = _parse_json(r.text) or {}
    llm_text = (r.text or "").strip()
    llm_eligible = parsed.get("eligible")
    if isinstance(llm_eligible, str):
        llm_eligible = llm_eligible.strip().lower() in ("true", "1", "yes")
    elif llm_eligible is not None:
        llm_eligible = bool(llm_eligible)

    eligible = eligible_data
    if false_denial:
        eligible = False
        source = "workshop_toggle"
    else:
        source = "workshop_default"

    if eligible:
        reason = f"Delivered {days:.0f} day(s) ago — within the {REFUND_WINDOW_DAYS}-day window."
    elif false_denial:
        reason = (parsed.get("reason") or "").strip() or (
            f"Delivered {int((days or 0) + REFUND_WINDOW_DAYS + 15)} days ago — "
            f"outside the {REFUND_WINDOW_DAYS}-day window."
        )
    elif order.get("status") != "DELIVERED":
        reason = "Only delivered orders can be refunded."
    else:
        reason = f"Outside the {REFUND_WINDOW_DAYS}-day return window."

    return {
        "eligible": eligible,
        "llm_eligible": llm_eligible,
        "reason": reason,
        "llm_response": llm_text,
        "source": source,
    }


def refund_abuse_screen(order: dict, *, config=None) -> dict:
    """Agente de ABUSE (LLM no Inspector). Decisão efetiva é **ALLOW** (sem toggle de dor).

    O LLM roda e o JSON dele fica em `llm_decision`/`llm_score`; a decisão aplicada
    está em `decision`/`allow`/`source` (Python, workshop-safe).
    """
    prompt = (
        f"Refund request for order {order['id']} totaling ${order.get('total', 0):.0f}. "
        'Screen for abuse. Reply ONLY with JSON {"decision": "ALLOW|BLOCK", "score": <0..1>}.'
        + _JSON_FENCE_SUFFIX
    )
    r = _run_agent_llm("abuse_check", prompt, verbose=False, config=config, workflow="returns")
    parsed = _parse_json(r.text) or {}
    llm_decision = str(parsed.get("decision", "")).strip().upper() or None
    llm_score = parsed.get("score")
    decision = "ALLOW"
    score = 0.05
    source = "workshop_default"
    return {
        "decision": decision,
        "allow": decision == "ALLOW",
        "score": score,
        "llm_decision": llm_decision,
        "llm_score": llm_score,
        "llm_response": (r.text or "").strip(),
        "source": source,
    }


def fraud_decision(quote: dict, total: float, *, config=None) -> dict:
    """Agente de FRAUDE (LLM no Inspector). Decisão efetiva é **workshop-safe** (F-OBS-PREP-7):

    - `fraud_false_positive` → **BLOCK** (erro de decisão do agente sobre dado correto).
    - senão → **ALLOW** (happy path estável).

    O LLM roda e o JSON dele fica em `llm_decision`/`llm_response` no retorno e no trace;
    a decisão que o checkout aplica está em `decision`/`source` (Python + toggle).
    """
    prompt = (f"Order total ${total:.0f}; price quote {json.dumps(quote)}. Assess fraud risk. "
              'Reply ONLY with JSON {"decision": "ALLOW|BLOCK", "score": <0..1>}.'
              + _JSON_FENCE_SUFFIX)
    r = _run_agent_llm("fraude", prompt, verbose=False, config=config, workflow="fulfillment")
    parsed = _parse_json(r.text) or {}
    llm_decision = str(parsed.get("decision", "")).strip().upper() or None
    llm_score = parsed.get("score")
    if FLAGS.fraud_false_positive:
        decision = "BLOCK"
        score = 0.95
        source = "workshop_toggle"
    else:
        decision = "ALLOW"
        score = 0.08
        source = "workshop_default"
    return {
        "decision": decision,
        "score": score,
        "allow": decision == "ALLOW",
        "llm_decision": llm_decision,
        "llm_score": llm_score,
        "llm_response": (r.text or "").strip(),
        "source": source,
    }


def build_graph():
    """Grafo de COMPRA (recomendação): hub-and-spoke concierge (coordinator → curator/respond)."""
    return build_concierge_graph()


def run_workflow(
    request="a birthday gift under $300",
    *,
    config=None,
):
    # Resolve a cascata UMA vez por execução, a partir da config corrente (F-020).
    cfgs = load_provider_configs()
    token_cfgs = _current_provider_cfgs.set(cfgs)
    token = _current_llm.set(get_llm())
    resolved = resolve_config(config, feature="concierge")
    token_rc = set_current_runnable_config(resolved)
    try:
        return build_graph().invoke(
            {"request": request, "messages": [], "trace": []},
            config=resolved,
        )
    finally:
        set_current_runnable_config(None, token_rc)
        _current_llm.reset(token)
        _current_provider_cfgs.reset(token_cfgs)


async def arun_workflow(
    request="a birthday gift under $300",
    *,
    config=None,
):
    cfgs = load_provider_configs()
    token_cfgs = _current_provider_cfgs.set(cfgs)
    token = _current_llm.set(get_llm())
    resolved = resolve_config(config, feature="concierge")
    token_rc = set_current_runnable_config(resolved)
    try:
        return await build_graph().ainvoke(
            {"request": request, "messages": [], "trace": []},
            config=resolved,
        )
    finally:
        set_current_runnable_config(None, token_rc)
        _current_llm.reset(token)
        _current_provider_cfgs.reset(token_cfgs)


def _messages_to_lc(messages: list[dict]) -> list:
    """Convert client chat messages to LangChain BaseMessage list."""
    lc: list = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "assistant":
            lc.append(AIMessage(content=content))
        else:
            lc.append(HumanMessage(content=content))
    return lc


async def arun_chat_workflow(
    messages: list[dict],
    context: dict | None = None,
    *,
    config=None,
):
    """Multi-turn chat workflow via build_chat_graph()."""
    cfgs = load_provider_configs()
    token_cfgs = _current_provider_cfgs.set(cfgs)
    token = _current_llm.set(get_llm())
    resolved = resolve_config(config, feature="chat")
    token_rc = set_current_runnable_config(resolved)
    lc_messages = _messages_to_lc(messages)
    if not lc_messages:
        return {
            "answer": "Please send a message.",
            "intent": "general",
            "artifacts": {},
            "language": None,
            "trace": [],
        }
    request = ""
    for m in reversed(lc_messages):
        if isinstance(m, HumanMessage):
            request = m.content if isinstance(m.content, str) else str(m.content)
            break
    ctx = context or {}
    try:
        return await build_chat_graph().ainvoke(
            {
                "request": request,
                "context_sku": ctx.get("sku", ""),
                "context_order_id": ctx.get("order_id", ""),
                "messages": lc_messages,
                "trace": [],
            },
            config=resolved,
        )
    finally:
        set_current_runnable_config(None, token_rc)
        _current_llm.reset(token)
        _current_provider_cfgs.reset(token_cfgs)
