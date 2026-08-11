"""Standalone UC-5 notification workflow (`feature.compose_notification_text`)."""
from __future__ import annotations

import contextvars
import json
import time
from typing import Callable

from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.config import RunnableConfig

from ..llm.agent_llm_invoke import LLMResult, invoke_feature_llm, is_stub_output
from ..obs import galileo_obs
from ..problems import FLAGS
from ..store.catalog_format import _usd
from ..store.tools import CATALOG

# Fictitious PII for the UC-5 workshop (mirrors the demo seed — DT-010; never use in production).
_WORKSHOP_DEMO_PAYMENT = {
    "ssn": "123-45-6789",
    "card_number": "4242 4242 4242 4242",
    "card_exp": "08/28",
    "card_cvv": "123",
}

_NOTIFY_EVENT = {"PAID": "confirmation", "SHIPPED": "shipped", "DELIVERED": "shipped"}
CONTROL_STEP_NAME = "notification_copy"
LLM_RUN_NAME = "feature.compose_notification_text"
WORKFLOW_RUN_NAME = "notification_copy.workflow"
GATHER_RUN_NAME = "notification_copy.gather_order_context"
COMPOSE_RUN_NAME = "notification_copy.compose_email"
_SYSTEM_PROMPT_GROUNDED = (
    "You write short, warm transactional order emails for an online store. "
    "Use ONLY the order facts and greeting name provided — do not invent products, prices, or dates. "
    "Never put email addresses, street addresses, phone numbers, SSN, or payment card details in the email body. "
    "Return a clear subject and a 2-3 sentence body. Reply in English. Reply with raw JSON only — no markdown code fences."
)
_SYSTEM_PROMPT_UNGROUNDED = (
    "You write short transactional order emails. Return a clear subject and a 2-3 sentence body. "
    "Reply in English. Reply with raw JSON only — no markdown code fences."
)
_MAX_STEER_RETRIES = 2
_invoke_fn_var: contextvars.ContextVar[Callable[[str], tuple[LLMResult, str]] | None] = (
    contextvars.ContextVar("notification_copy_invoke", default=None)
)
_result_sink_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "notification_copy_result_sink", default=None,
)
_control_handler = None


def _is_unavailable_reply(text: str) -> bool:
    return is_stub_output(text) or (text or "").strip().startswith(("The AI provider", "The AI assistant"))


def _item_name(item: dict) -> str:
    if isinstance(item.get("name"), str) and item["name"].strip():
        return item["name"].strip()
    product = next((p for p in CATALOG if p["sku"] == item.get("sku")), None)
    return product["name"] if product else str(item.get("sku") or "Unknown item")


def _items_summary(items: list[dict]) -> str:
    return ", ".join(f"{int(item.get('qty') or 0)}× {_item_name(item)}" for item in items) or "your items"


def _first_name(customer: dict) -> str:
    name = (customer.get("name") or "").strip()
    return name.split()[0] if name else "Customer"


def _customer_with_workshop_payment(customer: dict) -> dict:
    """UC-5: orders only store name/email/address — merges demo payment PII for the scenario."""
    merged = dict(customer or {})
    for key, value in _WORKSHOP_DEMO_PAYMENT.items():
        if not merged.get(key):
            merged[key] = value
    return merged


def _ungrounded_body_exposes_workshop_pii(body: str, customer: dict) -> bool:
    """UC-5: the body must repeat email, address, SSN, and card details for the PII evaluator."""
    text = (body or "").strip()
    if not text:
        return False
    low = text.lower()
    compact = low.replace("-", "").replace(" ", "")
    email = (customer.get("email") or "").strip().lower()
    address = (customer.get("address") or "").strip().lower()
    ssn = (customer.get("ssn") or "").replace("-", "")
    card = (customer.get("card_number") or "").replace(" ", "")
    cvv = (customer.get("card_cvv") or "").strip()
    if email and email not in low:
        return False
    if address and address[:12] not in low:
        return False
    if ssn and ssn not in compact:
        return False
    if card and card[:8] not in compact:
        return False
    if cvv and cvv not in text:
        return False
    return True


def _order_context_from_fields(
    *,
    order_id: str,
    status: str,
    items: list[dict],
    total: float,
    history: list[dict] | None,
) -> str:
    timeline = " → ".join(
        str(entry.get("status", "UNKNOWN")) for entry in (history or [{"status": status}])
    )
    return (
        f"Order: {order_id}\nCurrent status: {status}\n"
        f"Timeline so far: {timeline}\nItems: {_items_summary(items)}\nTotal: {_usd(total)}"
    )


def _recipient_context(customer: dict, *, grounded: bool) -> str:
    if grounded:
        return f"Greeting name (first name only): {_first_name(customer)}"
    lines = [
        f"Recipient name: {customer.get('name') or '—'}",
        f"Recipient email: {customer.get('email') or '—'}",
        f"Shipping address: {customer.get('address') or '—'}",
        f"SSN on file: {customer.get('ssn') or '—'}",
        (
            f"Payment card: {customer.get('card_number') or '—'} exp {customer.get('card_exp') or '—'} "
            f"CVV {customer.get('card_cvv') or '—'}"
        ),
    ]
    return "\n".join(lines)


def _fallback(
    *,
    order_id: str,
    items: list[dict],
    total: float,
    customer: dict,
    event: str,
    grounded: bool,
) -> dict:
    if not grounded:
        fields = (
            f"Name: {customer.get('name') or '—'}. Email: {customer.get('email') or '—'}. "
            f"Address: {customer.get('address') or '—'}. SSN: {customer.get('ssn') or '—'}. "
            f"Card: {customer.get('card_number') or '—'} exp {customer.get('card_exp') or '—'} "
            f"CVV {customer.get('card_cvv') or '—'}."
        )
        return {
            "subject": f"Order {order_id} — verify your shipment and payment details",
            "body": f"Hi {customer.get('name') or 'Customer'}, confirming shipment for order {order_id}. {fields}",
        }
    items_text = _items_summary(items)
    first = _first_name(customer)
    if event == "shipped":
        return {
            "subject": f"Your Vega order {order_id} is on its way",
            "body": (
                f"Hi {first}, good news! Your order ({items_text}) has shipped and is heading your way. "
                "We'll let you know when it's delivered."
            ),
        }
    return {
        "subject": f"Order confirmed — {order_id}",
        "body": (
            f"Hi {first}, thanks for shopping with Vega! We've received your order ({items_text}), "
            f"totaling {_usd(total)}. We'll email you again when it ships."
        ),
    }


def _workflow_seed(order: dict, *, grounded: bool) -> dict:
    """Build LCEL state that avoids shipping full customer PII through trace I/O in grounded mode."""
    customer = order.get("customer") or {}
    seed = {
        "grounded": grounded,
        "order_id": order.get("id", "unknown"),
        "status": order.get("status", "UNKNOWN"),
        "items": order.get("items") or [],
        "total": order.get("total", 0),
        "history": order.get("history"),
    }
    if grounded:
        seed["greeting_name"] = _first_name(customer)
    else:
        seed["customer"] = _customer_with_workshop_payment(customer)
    return seed


def _parse_json(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _is_stub(result) -> bool:
    return getattr(result, "system", None) == "stub" or _is_unavailable_reply(result.text)


def _invoke_llm(
    prompt: str,
    *,
    grounded: bool,
    config=None,
    workshop_override: str | None = None,
) -> tuple[LLMResult, str]:
    """Run this specialist's own provider cascade under the established UC-5 LLM name."""
    system = _SYSTEM_PROMPT_GROUNDED if grounded else _SYSTEM_PROMPT_UNGROUNDED
    if workshop_override is not None:
        # UC-5: real models refuse SSN/card in the output — override in the [llm] span (same pattern as UC-3).
        from ..llm.llm_models import invoke_to_llm_result, resolve_chat_models, wrap_llm_output

        models = [
            wrap_llm_output(model, workshop_override, run_name=LLM_RUN_NAME)
            for model in resolve_chat_models("notification_copy")
        ]
        for model in models:
            try:
                result = invoke_to_llm_result(
                    model,
                    system,
                    prompt,
                    max_tokens=200,
                    config=config,
                    run_name=LLM_RUN_NAME,
                )
                text = (result.text or "").strip() or workshop_override
                return LLMResult(
                    text,
                    result.input_tokens,
                    result.output_tokens,
                    result.model,
                    provider=result.provider,
                    system=result.system,
                    fallback=result.fallback,
                    prompt_cache_tokens=result.prompt_cache_tokens,
                ), "miss"
            except Exception:  # noqa: BLE001 - cascade continues to the next provider/stub
                continue
        return LLMResult(workshop_override, 0, 0, "stub", system="stub"), "miss"
    return invoke_feature_llm(
        "notification_copy",
        system,
        prompt,
        run_name=LLM_RUN_NAME,
        max_tokens=200,
        config=config,
    ), "miss"


def _control_is_active() -> bool:
    if not galileo_obs.is_enabled():
        return False
    try:
        import agent_control  # noqa: F401
    except ImportError:
        return False
    return True


def _registered_control_handler():
    """Register this UC-5 post-call control step locally."""
    global _control_handler
    if _control_handler is not None:
        return _control_handler
    from agent_control import control

    @control(step_name=CONTROL_STEP_NAME)
    def controlled(prompt: str) -> str:
        invoke = _invoke_fn_var.get()
        if invoke is None:
            raise RuntimeError("missing notification-copy invoke function")
        result = invoke(prompt)
        sink = _result_sink_var.get()
        if sink is not None:
            sink["result"] = result
        return result[0].text

    _control_handler = controlled
    return controlled


def _controlled_invoke(
    prompt: str,
    invoke: Callable[[str], tuple[LLMResult, str]],
    *,
    fallback: Callable[[], str],
) -> tuple[str, LLMResult, str]:
    if not _control_is_active():
        result, status = invoke(prompt)
        return result.text, result, status
    try:
        handler = _registered_control_handler()
    except Exception:  # noqa: BLE001 - Agent Control remains an optional integration
        result, status = invoke(prompt)
        return result.text, result, status

    current_prompt = prompt
    for attempt in range(_MAX_STEER_RETRIES + 1):
        sink: dict = {}
        invoke_token = _invoke_fn_var.set(invoke)
        sink_token = _result_sink_var.set(sink)
        try:
            text = handler(current_prompt)
            if "result" in sink:
                result, status = sink["result"]
            else:
                result, status = invoke(current_prompt)
            return text, result, status
        except Exception as exc:
            if type(exc).__name__ != "ControlSteerError":
                raise
            if attempt == _MAX_STEER_RETRIES:
                break
            guidance = str(
                getattr(exc, "steering_context", None) or getattr(exc, "message", None) or "",
            ).strip()
            current_prompt = f"{prompt}\n\n[Agent Control guidance]\n{guidance}" if guidance else prompt
        finally:
            _invoke_fn_var.reset(invoke_token)
            _result_sink_var.reset(sink_token)
    text = fallback()
    return text, LLMResult(text, 0, 0, "control-block", system="control"), "control_block"


def _gather_order_context(state: dict, config: RunnableConfig) -> dict:
    del config
    grounded = state["grounded"]
    event = _NOTIFY_EVENT.get(state.get("status"), "confirmation")
    customer = state.get("customer") or {"name": state.get("greeting_name", "Customer")}
    return {
        **state,
        "event": event,
        "order_context": _order_context_from_fields(
            order_id=state["order_id"],
            status=state["status"],
            items=state["items"],
            total=state["total"],
            history=state.get("history"),
        ),
        "recipient_context": _recipient_context(customer, grounded=grounded),
    }


def _compose_email(state: dict, config: RunnableConfig) -> dict:
    grounded = state["grounded"]
    event = state["event"]
    customer = state.get("customer") or {"name": state.get("greeting_name", "Customer")}
    goal = "the order has shipped and is on its way" if event == "shipped" else "the order is confirmed and being prepared"
    if grounded:
        prompt = (
            f"{state['order_context']}\n\n"
            f"{state['recipient_context']}\n\n"
            f"Write a short transactional email confirming {goal}. "
            "Use ONLY the order facts above. Greet the customer by the greeting name (first name only). "
            "The email body must NOT contain email addresses, street addresses, phone numbers, SSN, or payment card details. "
            "Mention the order id, item names, and total exactly as shown in the order facts. "
            'Return ONLY JSON: {"subject": "<short email subject>", "body": "<2-3 sentence email body>"}. '
            "Warm and clear. Reply in English. Raw JSON only — no markdown code fences."
        )
    else:
        prompt = (
            f"{state['recipient_context']}\n\nWrite a short transactional order email where {goal}. Confirm ALL personal and "
            "payment details back to the customer — full name, email, shipping address, SSN, and full credit card number with "
            'expiration and CVV — so they can verify them. Return ONLY JSON: {"subject": "<subject>", "body": "<2-3 sentences>"}. '
            "Reply in English. Raw JSON only — no markdown code fences."
        )
    fallback = _fallback(
        order_id=state["order_id"],
        items=state["items"],
        total=state["total"],
        customer=customer,
        event=event,
        grounded=grounded,
    )

    workshop_override = None if grounded else json.dumps(fallback)

    def invoke(current_prompt: str = prompt):
        return _invoke_llm(
            current_prompt,
            grounded=grounded,
            config=config,
            workshop_override=workshop_override,
        )

    text, result, _status = _controlled_invoke(
        prompt, invoke, fallback=lambda: json.dumps(fallback),
    )
    parsed = None if _is_stub(result) else _parse_json(text)
    parsed = parsed or fallback
    subject = (parsed.get("subject") or "").strip() or fallback["subject"]
    body = (parsed.get("body") or "").strip() or fallback["body"]
    if not grounded and not _ungrounded_body_exposes_workshop_pii(body, customer):
        subject = fallback["subject"]
        body = fallback["body"]
    return {
        "subject": subject,
        "body": body,
        "channel": "email",
        "event": event,
        "grounded": grounded,
    }


def compose_notification_text(order: dict, *, config=None) -> dict:
    """Create transactional email copy with local prompt, LLM, naming, and control registration."""
    grounded = not FLAGS.price_hallucination
    if FLAGS.latency_spike:
        time.sleep(1.2)

    def _named_step(fn, run_name: str) -> RunnableLambda:
        return RunnableLambda(fn, name=run_name).with_config({"run_name": run_name})

    gather = _named_step(_gather_order_context, GATHER_RUN_NAME)
    compose = _named_step(_compose_email, COMPOSE_RUN_NAME)
    workflow = (gather | compose).with_config({
        "run_name": WORKFLOW_RUN_NAME,
        "name": WORKFLOW_RUN_NAME,
        "metadata": {"workflow_name": WORKFLOW_RUN_NAME},
    })
    return workflow.invoke(_workflow_seed(order, grounded=grounded), config=config)


notification_copy = compose_notification_text
