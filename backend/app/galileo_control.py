"""Agent Control / Protect (ADR-033) — enforcement em runtime, opt-in pela mesma credencial Splunk Agent Observability.

Único módulo que importa `agent_control`. Sem `GALILEO_API_KEY` (ou init falhou / pacote ausente) →
no-op; a loja segue idêntica à demo base. Rulesets ficam no Console, não em código.
"""
from __future__ import annotations

import contextvars
import logging
import os
from typing import Any, Callable

from . import galileo_obs
from .llm import LLMResult
from .problems import FLAGS
from .tools import REFUND_WINDOW_DAYS

log = logging.getLogger(__name__)

_initialized = False
_warned = False
_decorated = False

CONTROL_FEATURES_PRE = frozenset({"product_qa", "search"})
CONTROL_FEATURES_POST = frozenset({"notification_copy", "gift_message"})
CONTROLLED_FEATURES = CONTROL_FEATURES_PRE | CONTROL_FEATURES_POST

_invoke_fn_var: contextvars.ContextVar[Callable[[], Any] | None] = contextvars.ContextVar(
    "_galileo_control_invoke_fn", default=None,
)
_last_llm_var: contextvars.ContextVar[tuple[LLMResult, str] | None] = contextvars.ContextVar(
    "_galileo_control_last_llm", default=None,
)

_PRODUCT_QA_BLOCK_TEXT = (
    "I can only help with product details from our catalog. "
    "Please ask about this item's features, price, or availability."
)
_SEARCH_BLOCK_TEXT = (
    '{"skus": [], "interpretation": "I couldn\'t process that search. '
    'Try describing what you\'re looking for.", "did_you_mean": null}'
)

_STEER_MAX_RETRIES = 2


def is_enabled() -> bool:
    return galileo_obs.is_enabled()


def is_active() -> bool:
    return _initialized


def register_steps() -> list[dict[str, str]]:
    return [
        {"type": "tool", "name": "delete_product"},
        {"type": "tool", "name": "returns.finalize"},
        {"type": "llm", "name": "product_qa"},
        {"type": "llm", "name": "search"},
        {"type": "llm", "name": "notification_copy"},
        {"type": "llm", "name": "gift_message"},
    ]


def _warn_once(exc: Exception) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("Agent Control desabilitado nesta execução (%s: %s)", type(exc).__name__, exc)


def _agent_control_url() -> str:
    explicit = os.getenv("AGENT_CONTROL_URL", "").strip()
    if explicit:
        return explicit
    console = os.getenv("GALILEO_CONSOLE_URL", "https://console.multitenant.galileocloud.io").strip()
    if "console." in console:
        return console.replace("console.", "agent-control.", 1)
    return "https://agent-control.multitenant.galileocloud.io"


def init_once() -> None:
    global _initialized
    if _initialized or not galileo_obs.is_enabled():
        return
    try:
        import agent_control
        from agent_control.settings import configure_settings
        from galileo import galileo_context, get_agent_control_target

        galileo_context.init(project=galileo_obs.project(), log_stream=galileo_obs.log_stream())
        target = get_agent_control_target()
        api_key = os.getenv("GALILEO_API_KEY", "").strip()
        server_url = _agent_control_url()
        agent_control.init(
            agent_name="vega-concierge",
            agent_description="Vega Concierge workshop store — Agent Control",
            server_url=server_url,
            api_key=api_key,
            api_key_header=os.getenv("AGENT_CONTROL_API_KEY_HEADER", "Galileo-API-Key"),
            target_type=target.target_type,
            target_id=target.target_id,
            steps=register_steps(),
            observability_enabled=True,
            observability_sink_name="registered",
        )
        # `agent_control.init(server_url=...)` only sets `_state.state.server_url`, which the
        # step evaluation call (`control_decorators._get_server_url()`) never reads — that path
        # pulls from the SDK's own pydantic-settings singleton (`AGENT_CONTROL_URL` env var,
        # default `http://localhost:8000`). Without this, every `@control`-decorated step call
        # (delete_product, returns.finalize, ...) silently posts to our OWN API port and 404s,
        # which the tool call then swallows into a generic "unknown reason" error — no Block/Steer
        # ever reaches the trace.
        configure_settings(url=server_url, api_key=api_key)
        _ensure_decorated_steps()
        _initialized = True
        log.info("Agent Control inicializado (target=%s/%s)", target.target_type, target.target_id)
    except Exception as exc:  # noqa: BLE001
        _warn_once(exc)


def _control_block_result(feature: str, safe_text: str) -> tuple[str, LLMResult, str]:
    r = LLMResult(
        safe_text, 0, 0, "control-block",
        provider="Splunk Agent Observability", system="control", fallback=False,
    )
    return safe_text, r, "control_block"


def _safe_pre_block_text(feature: str) -> str:
    if feature == "search":
        return _SEARCH_BLOCK_TEXT
    return _PRODUCT_QA_BLOCK_TEXT


def _ensure_decorated_steps() -> None:
    global _decorated
    if _decorated:
        return
    from agent_control import control

    def _llm_controlled(prompt: str) -> str:
        fn = _invoke_fn_var.get()
        if fn is None:
            raise RuntimeError("missing invoke fn for control step")
        result = fn()
        if isinstance(result, tuple) and len(result) == 2 and hasattr(result[0], "text"):
            r, status = result
            _last_llm_var.set((r, status))
            return r.text
        raise RuntimeError("invoke fn must return (LLMResult, status)")

    @control(step_name="product_qa")
    def _product_qa_controlled(prompt: str) -> str:
        return _llm_controlled(prompt)

    @control(step_name="search")
    def _search_controlled(prompt: str) -> str:
        return _llm_controlled(prompt)

    @control(step_name="notification_copy")
    def _notification_copy_controlled(prompt: str) -> str:
        return _llm_controlled(prompt)

    @control(step_name="gift_message")
    def _gift_message_controlled(prompt: str) -> str:
        return _llm_controlled(prompt)

    @control(step_name="returns.finalize")
    def _returns_finalize_controlled(_payload: dict) -> dict:
        fn = _invoke_fn_var.get()
        if fn is None:
            raise RuntimeError("missing invoke fn for returns.finalize control")
        outcome = fn()
        if isinstance(outcome, tuple):
            return outcome[0]
        return outcome

    @control(step_name="delete_product")
    def _delete_product_controlled(_payload: dict) -> dict:
        fn = _invoke_fn_var.get()
        if fn is None:
            raise RuntimeError("missing invoke fn for delete_product control")
        outcome = fn()
        if isinstance(outcome, tuple):
            return outcome[0]
        return outcome

    _returns_finalize_controlled.name = "returns.finalize"
    _returns_finalize_controlled.tool_name = "returns.finalize"
    _delete_product_controlled.name = "delete_product"
    _delete_product_controlled.tool_name = "delete_product"

    globals().update({
        "_product_qa_controlled": _product_qa_controlled,
        "_search_controlled": _search_controlled,
        "_notification_copy_controlled": _notification_copy_controlled,
        "_gift_message_controlled": _gift_message_controlled,
        "_returns_finalize_controlled": _returns_finalize_controlled,
        "_delete_product_controlled": _delete_product_controlled,
    })
    _decorated = True


def _pre_handler(feature: str):
    _ensure_decorated_steps()
    return globals()[f"_{feature}_controlled"]


def _post_handler(feature: str):
    _ensure_decorated_steps()
    return globals()[f"_{feature}_controlled"]


def controlled_feature_invoke(
    feature: str,
    prompt: str,
    invoke_fn: Callable[[], tuple[LLMResult, str]],
    *,
    chain_invoke: Callable[[str], tuple[LLMResult, str]],
    control_fallback: Callable[[], str] | None = None,
) -> tuple[str, LLMResult, str]:
    """Choke point UC-4 (pre Block) e UC-5 (post Steer + retry)."""
    if not is_active() or feature not in CONTROLLED_FEATURES:
        r, status = invoke_fn()
        return r.text, r, status

    from agent_control import ControlSteerError, ControlViolationError

    if feature in CONTROL_FEATURES_PRE:
        token = _invoke_fn_var.set(invoke_fn)
        try:
            text = _pre_handler(feature)(prompt)
            r, status = _last_llm_var.get() or invoke_fn()
            return text, r, status
        except ControlViolationError:
            return _control_block_result(feature, _safe_pre_block_text(feature))
        finally:
            _invoke_fn_var.reset(token)
            _last_llm_var.set(None)

    handler = _post_handler(feature)
    current_prompt = prompt
    last_err: ControlSteerError | None = None
    for attempt in range(_STEER_MAX_RETRIES + 1):
        def _bound_invoke(p: str = current_prompt) -> tuple[LLMResult, str]:
            return chain_invoke(p)

        token = _invoke_fn_var.set(_bound_invoke)
        _last_llm_var.set(None)
        try:
            text = handler(current_prompt)
            pack = _last_llm_var.get()
            if pack is None:
                pack = chain_invoke(current_prompt)
            r, status = pack
            return text, r, status
        except ControlSteerError as err:
            last_err = err
            if attempt >= _STEER_MAX_RETRIES:
                break
            steer = (err.steering_context or err.message or "").strip()
            current_prompt = (
                f"{prompt}\n\n[Agent Control guidance]\n{steer}" if steer else prompt
            )
        finally:
            _invoke_fn_var.reset(token)
            _last_llm_var.set(None)

    if control_fallback is not None:
        return _control_block_result(feature, control_fallback())
    if last_err is not None:
        log.info("Steer esgotado em %s (%s)", feature, last_err.control_name)
    r, status = chain_invoke(prompt)
    return r.text, r, status


def controlled_delete_product(
    sku: str,
    compute_fn: Callable[[], dict],
    *,
    prompt_snippet: str | None = None,
) -> dict:
    """UC-4 — Block em `delete_product` (step tool, avaliação pre — bloqueia antes de mutar)."""
    if not is_active():
        return compute_fn()

    from agent_control import ControlViolationError

    _ensure_decorated_steps()
    payload: dict[str, Any] = {"sku": sku}
    if prompt_snippet:
        payload["prompt_snippet"] = prompt_snippet

    token = _invoke_fn_var.set(compute_fn)
    try:
        try:
            return _delete_product_controlled(payload)
        except ControlViolationError as err:
            reason = (getattr(err, "message", None) or str(err)).strip()
            return {"deleted": False, "blocked": True, "sku": sku, "reason": reason or "blocked"}
    finally:
        _invoke_fn_var.reset(token)


def controlled_finalize_refund(
    order: dict,
    compute_fn: Callable[[], dict],
    *,
    corrected_fn: Callable[[], dict],
) -> dict:
    """UC-3 — Block em `returns.finalize` (step tool, avaliação post)."""
    if not is_active():
        return compute_fn()

    from agent_control import ControlViolationError

    _ensure_decorated_steps()
    outcome = compute_fn()
    payload = _finalize_control_payload(order, outcome)

    token = _invoke_fn_var.set(lambda: outcome)
    try:
        try:
            return _returns_finalize_controlled(payload)
        except ControlViolationError:
            return corrected_fn()
    finally:
        _invoke_fn_var.reset(token)


def _finalize_control_payload(order: dict, outcome: dict) -> dict:
    from .graphs.returns import _days_since_delivery

    days = _days_since_delivery(order)
    return {
        "order_id": order.get("id"),
        "order_status": order.get("status"),
        "days_since_delivery": days,
        "refund_window_days": REFUND_WINDOW_DAYS,
        "refund_false_denial_toggle": FLAGS.refund_false_denial,
        "data_eligible": (
            order.get("status") == "DELIVERED"
            and days is not None
            and days <= REFUND_WINDOW_DAYS
        ),
        "eligible": outcome.get("eligible"),
        "approved": outcome.get("approved"),
        "reason": outcome.get("reason"),
        "steps": outcome.get("steps"),
    }
