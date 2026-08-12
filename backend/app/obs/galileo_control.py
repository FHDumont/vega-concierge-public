"""Agent Control / Protect (ADR-033) — runtime enforcement, opt-in via same Splunk Agent Observability credential.

Only module importing `agent_control`. Without `GALILEO_API_KEY` (or init failed / package absent) →
no-op; store stays identical to base demo. Rulesets live in Console, not code.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from .. import ai_agents as _registered_ai_agents  # noqa: F401 - publishes local runtime boundaries
from . import galileo_obs
from ..llm.llm import LLMResult
from ..platform.agent_runtime import control_features, registered_control_steps
from ..problems import FLAGS
from ..store.tools import REFUND_WINDOW_DAYS
from ..settings import settings

log = logging.getLogger(__name__)

_T = TypeVar("_T")
_control_executor: ThreadPoolExecutor | None = None

_initialized = False
_warned = False
_decorated = False

# Backward-compatible read-only views for legacy call sites. Agent package owns
# declarations; adapter only consumes them.
# delete_product runs through controlled_delete_product(), not controlled_feature_invoke().
_CONTROLLED_LLM_PRE = control_features("pre")
_CONTROL_STANDALONE_PRE = frozenset({"delete_product", "list_recent_customers"})
CONTROL_FEATURES_PRE = _CONTROLLED_LLM_PRE - _CONTROL_STANDALONE_PRE
CONTROL_FEATURES_POST = control_features("post")
CONTROLLED_FEATURES = CONTROL_FEATURES_PRE | CONTROL_FEATURES_POST

_invoke_fn_var: contextvars.ContextVar[Callable[[], Any] | None] = contextvars.ContextVar(
    "_galileo_control_invoke_fn", default=None,
)
# agent_control's `@control` runs decorated step inside `asyncio.run(...)`
# (`agent_control/control_decorators.py:862`), and **writes** to ContextVar inside new context
# do NOT propagate back to caller — only reads work (context is copied in).
# ContextVar carrying result VALUE always came back `None` here, and
# code fell to fallback, invoking LLM SECOND time: double cost and entire subtree
# duplicated in trace (measured in Console — F-WORKSHOP-STAB-4). What crosses boundary is
# MUTABLE container: dict created here, step writes in there, mutation
# visible because same object.
_result_sink_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "_galileo_control_result_sink", default=None,
)

# Workshop control seeded on boot (F-GALILEO-CTRL-1) — module 8 exercise. Its condition reads the
# **value** of the Prompt Injection (SLM) metric and denies past the threshold; it is NOT a regex.
# The name carries the log stream's because control names are unique per org (create returns 409).
WORKSHOP_CONTROL_NAME = "vega-prompt-injection"    # suffixed with the log stream name
WORKSHOP_CONTROL_SCORER = "prompt_injection_luna"  # "Prompt Injection (SLM)"
WORKSHOP_CONTROL_THRESHOLD = "0.7"                 # string — that's how the API stores it
WORKSHOP_CONTROL_TIMEOUT_MS = 10_000

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


def run_control_step(fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
    """Run an ``@control``-decorated step without nesting ``asyncio.run`` inside a live loop.

    FastAPI ``async def`` handlers and LangGraph ``ainvoke`` already run inside an event loop;
    ``agent_control``'s sync ``@control`` wrapper calls ``asyncio.run(...)`` and raises
    ``RuntimeError: asyncio.run() cannot be called from a running event loop``.  When a loop is
    active, execute the decorated step on a worker thread (no loop there).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return fn(*args, **kwargs)
    ctx = contextvars.copy_context()
    return _executor().submit(lambda: ctx.run(fn, *args, **kwargs)).result()


def _executor() -> ThreadPoolExecutor:
    global _control_executor
    if _control_executor is None:
        _control_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="galileo-control")
    return _control_executor


def _run_async(coro_factory: Callable[[], Any]) -> Any:
    """Run a coroutine from sync code that may already be inside a live event loop.

    `_bootstrap()` runs inside the FastAPI `lifespan` (async), so `asyncio.run` here would raise
    `RuntimeError: asyncio.run() cannot be called from a running event loop`. Same escape hatch as
    `run_control_step`: with a loop active, `asyncio.run` goes to a worker thread (no loop there).
    The factory — not the coroutine — is passed so it's created in the thread that awaits it.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())
    return _executor().submit(lambda: asyncio.run(coro_factory())).result()


def register_steps() -> list[dict[str, str]]:
    """Translate local agent registrations to Agent Control's SDK payload."""
    return [step.as_dict() for step in registered_control_steps()]


def _warn_once(exc: Exception) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("Agent Control disabled for this run (%s: %s)", type(exc).__name__, exc)


def _workshop_control_data(scorer_id: str, version_id: str, label: str) -> dict:
    """The control definition — same shape the Console produces for a Prompt Injection control."""
    return {
        "condition": {
            "selector": {"path": "*"},
            "evaluator": {
                "name": "galileo.luna",
                "config": {
                    "operator": "gte",
                    "threshold": WORKSHOP_CONTROL_THRESHOLD,
                    "timeout_ms": WORKSHOP_CONTROL_TIMEOUT_MS,
                    "payload_field": "input",
                    "scorer_id": scorer_id,
                    "scorer_label": label,
                    "scorer_version_id": version_id,
                },
            },
            "and": None, "or": None, "not": None,
        },
        "description": None,
        "enabled": True,
        "execution": "server",
        "scope": {
            "step_types": ["tool", "llm"],
            "step_names": None,
            "step_name_regex": None,
            "stages": ["pre"],
        },
        "action": {"decision": "deny", "steering_context": None},
        "tags": [],
        "template": None,
        "template_values": None,
    }


def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


async def _ensure_stream_control_async(target: Any, name: str) -> None:
    from agent_control import control_bindings
    from agent_control.client import AgentControlClient
    from agent_control.controls import create_control, list_controls

    client = AgentControlClient(
        base_url=galileo_obs.agent_control_url(),
        api_key=settings.galileo_api_key.strip(),
        api_key_header=settings.agent_control_api_key_header,
    )
    async with client:
        # The API key is scoped: listing controls without the target filter answers 401.
        listing = await list_controls(
            client,
            limit=100,
            include_attachments=True,
            attachment_target_type=target.target_type,
            attachment_target_id=target.target_id,
        )
        for control in listing.get("controls") or []:
            control = control or {}
            # `attachment_target_*` is documented as a filter "for attachments", so a homonymous
            # control that is NOT bound to this stream could still come back in the list. Returning
            # on the name alone would then leave the stream binding-less forever, silently — hence
            # the check that this control actually carries an attachment.
            if control.get("name") == name and control.get("attachments"):
                # Already attached: whatever the attendee toggled or edited in Console is theirs.
                return

        ref = galileo_obs.preset_scorer_ref(WORKSHOP_CONTROL_SCORER)
        if ref is None:
            log.error(
                "galileo: preset scorer %r not resolved — workshop control not created",
                WORKSHOP_CONTROL_SCORER,
            )
            return
        scorer_id, version_id, label = ref

        try:
            created = await create_control(
                client, name, _workshop_control_data(scorer_id, version_id, label),
            )
        except Exception as exc:  # noqa: BLE001
            if _status_code(exc) == 409:
                # Name taken in the org but not attached here (e.g. a previous boot's bind failed).
                # A second try under another name would only breed junk — the owner fixes it in Console.
                log.warning("galileo: control %r already exists in the org and is not attached "
                            "to this log stream — resolve it in the Console", name)
                return
            raise
        control_id = created.get("control_id")
        if not control_id:
            # Binding a `None` id would just 422 and degrade into a generic warning.
            log.error("galileo: create_control returned no control_id for %r (%r)", name, created)
            return

        # The Console's on/off toggle moves the BINDING, not the control: seed it attached and off.
        await control_bindings.upsert_control_binding_by_key(
            client,
            target_type=target.target_type,
            target_id=target.target_id,
            control_id=control_id,
            enabled=False,
        )
    log.info(
        "galileo: control %r created and attached (disabled) to log stream %r",
        name, galileo_obs.log_stream(),
    )


def ensure_stream_control(target: Any) -> None:
    """Gives the log stream the workshop's prompt-injection control — boot-time, idempotent, best effort.

    Sibling of `galileo_obs.ensure_stream_metrics`: the control lives in the Console, so each
    attendee's brand new log stream would otherwise have none, and module 8 has nothing to toggle.
    It is seeded **disabled** — the exercise is turning it on. An existing one is never touched.
    """
    name = f"{WORKSHOP_CONTROL_NAME}-{galileo_obs.log_stream()}"
    try:
        _run_async(lambda: _ensure_stream_control_async(target, name))
    except Exception as exc:  # noqa: BLE001 — Galileo unreachable can't block the boot
        log.warning("galileo: ensure_stream_control skipped (%s: %s)", type(exc).__name__, exc)


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
        # Own try/except: seeding the workshop control must never keep Agent Control from starting.
        try:
            ensure_stream_control(target)
        except Exception as exc:  # noqa: BLE001
            log.warning("galileo: ensure_stream_control failed (%s: %s)", type(exc).__name__, exc)
        api_key = settings.galileo_api_key.strip()
        server_url = galileo_obs.agent_control_url()
        agent_control.init(
            agent_name="vega-concierge",
            agent_description="Vega Concierge workshop store — Agent Control",
            server_url=server_url,
            api_key=api_key,
            api_key_header=settings.agent_control_api_key_header,
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
        log.info("Agent Control initialized (target=%s/%s)", target.target_type, target.target_id)
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
            sink = _result_sink_var.get()
            if sink is not None:
                # Mutation, not `set()`: only the dict crosses the `@control`'s `asyncio.run`.
                sink["llm"] = (r, status)
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
    def _delete_product_controlled(prompt: str) -> dict:
        fn = _invoke_fn_var.get()
        if fn is None:
            raise RuntimeError("missing invoke fn for delete_product control")
        outcome = fn()
        if isinstance(outcome, tuple):
            return outcome[0]
        return outcome

    @control(step_name="list_recent_customers")
    def _list_recent_customers_controlled(prompt: str) -> list[dict] | dict:
        del prompt
        fn = _invoke_fn_var.get()
        if fn is None:
            raise RuntimeError("missing invoke fn for list_recent_customers control")
        return fn()

    _returns_finalize_controlled.name = "returns.finalize"
    _returns_finalize_controlled.tool_name = "returns.finalize"

    globals().update({
        "_product_qa_controlled": _product_qa_controlled,
        "_search_controlled": _search_controlled,
        "_notification_copy_controlled": _notification_copy_controlled,
        "_returns_finalize_controlled": _returns_finalize_controlled,
        "_delete_product_controlled": _delete_product_controlled,
        "_list_recent_customers_controlled": _list_recent_customers_controlled,
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
    """Choke point for UC-4 (pre Block) and UC-5 (post Steer + retry)."""
    if not is_active() or feature not in CONTROLLED_FEATURES:
        r, status = invoke_fn()
        return r.text, r, status

    from agent_control import ControlSteerError, ControlViolationError

    if feature in CONTROL_FEATURES_PRE:
        sink: dict = {}
        token = _invoke_fn_var.set(invoke_fn)
        sink_token = _result_sink_var.set(sink)
        try:
            text = run_control_step(_pre_handler(feature), prompt)
            # `invoke_fn()` again here only if the step did NOT actually run the chain (never on
            # the normal path) — it's a safety net, not the expected path.
            r, status = sink.get("llm") or invoke_fn()
            return text, r, status
        except ControlViolationError:
            return _control_block_result(feature, _safe_pre_block_text(feature))
        finally:
            _invoke_fn_var.reset(token)
            _result_sink_var.reset(sink_token)

    handler = _post_handler(feature)
    current_prompt = prompt
    last_err: ControlSteerError | None = None
    for attempt in range(_STEER_MAX_RETRIES + 1):
        def _bound_invoke(p: str = current_prompt) -> tuple[LLMResult, str]:
            return chain_invoke(p)

        sink: dict = {}
        token = _invoke_fn_var.set(_bound_invoke)
        sink_token = _result_sink_var.set(sink)
        try:
            text = run_control_step(handler, current_prompt)
            pack = sink.get("llm")
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
            _result_sink_var.reset(sink_token)

    if control_fallback is not None:
        return _control_block_result(feature, control_fallback())
    if last_err is not None:
        log.info("Steer exhausted on %s (%s)", feature, last_err.control_name)
    r, status = chain_invoke(prompt)
    return r.text, r, status


def controlled_delete_product(
    sku: str,
    compute_fn: Callable[[], dict],
    *,
    prompt_snippet: str | None = None,
) -> dict:
    """UC-4 — Block on `delete_product` (step tool, pre evaluation — blocks before mutating)."""
    if not is_active():
        return compute_fn()

    from agent_control import ControlViolationError

    _ensure_decorated_steps()
    snippet = (prompt_snippet or "").strip() or f"delete product {sku}"

    token = _invoke_fn_var.set(compute_fn)
    try:
        try:
            result = run_control_step(_delete_product_controlled, snippet)
            return result
        except ControlViolationError as err:
            reason = (getattr(err, "message", None) or str(err)).strip()
            return {"deleted": False, "blocked": True, "sku": sku, "reason": reason or "blocked"}
    finally:
        _invoke_fn_var.reset(token)


def controlled_list_recent_customers(
    prompt: str,
    compute_fn: Callable[[], list[dict]],
) -> list[dict] | dict:
    """UC-4 — Block on ``list_recent_customers`` (pre — before leaking PII)."""
    if not is_active():
        return compute_fn()

    from agent_control import ControlViolationError

    _ensure_decorated_steps()
    snippet = (prompt or "").strip() or "export customer records"

    token = _invoke_fn_var.set(compute_fn)
    try:
        try:
            result = run_control_step(_list_recent_customers_controlled, snippet)
            return result
        except ControlViolationError as err:
            reason = (getattr(err, "message", None) or str(err)).strip()
            return {"blocked": True, "customers": [], "reason": reason or "blocked"}
    finally:
        _invoke_fn_var.reset(token)


def controlled_finalize_refund(
    order: dict,
    compute_fn: Callable[[], dict],
    *,
    corrected_fn: Callable[[], dict],
) -> dict:
    """UC-3 — Block on `returns.finalize` (step tool, post evaluation)."""
    if not is_active():
        return compute_fn()

    from agent_control import ControlViolationError

    _ensure_decorated_steps()
    outcome = compute_fn()
    payload = _finalize_control_payload(order, outcome)

    token = _invoke_fn_var.set(lambda: outcome)
    try:
        try:
            return run_control_step(_returns_finalize_controlled, payload)
        except ControlViolationError:
            return corrected_fn()
    finally:
        _invoke_fn_var.reset(token)


def _finalize_control_payload(order: dict, outcome: dict) -> dict:
    delivered_at = None
    for event in order.get("history", []):
        if event.get("status") == "DELIVERED":
            try:
                delivered_at = datetime.fromisoformat(event["at"])
            except (KeyError, TypeError, ValueError):
                pass
            break
    days = (
        (datetime.now(timezone.utc) - delivered_at).total_seconds() / 86400.0
        if delivered_at is not None
        else None
    )
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
