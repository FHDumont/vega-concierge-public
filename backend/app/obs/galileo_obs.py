"""Splunk Agent Observability integration (ADR-032) — opt-in via presence of `GALILEO_API_KEY`.

Pattern from `healthcare-assistant/2-app-with-instrumentation`: `galileo_context(project, log_stream)`
+ `start_session(external_id=...)` per request, and **one `GalileoAsyncCallback` per request** (each
user turn becomes own trace). No `@log` decorator or `galileo.openai` wrapper.

Without `GALILEO_API_KEY` — or without `galileo` package installed — everything here is no-op and app runs
identical to prior behavior: it's workshop "base" demo. SDK import is lazy and guarded
precisely so `requirements.txt` doesn't become prerequisite to running store.

**Evaluators**: on boot, `ensure_stream_metrics()` creates the log stream (when it doesn't exist
yet) with the workshop's core evaluator set pre-enabled — an existing stream is never touched, so
changes made in Console stick. Everything else about evaluators stays in Console.

Readable span labels (LLM `name=`, ReAct nodes, `run_name` in chains) live in `galileo_span.py`;
this module delivers callback, session context, and — since F-BACKEND-3 (D.3) — **live trace**
of request: `start_trace` right after `start_session`, `conclude`+flush in `finally`. It's
open trace that gives `current_parent()` to SDK throughout request, without which Agent
Control has nowhere to hang `[control]` span (callback's batch mode only materializes spans
on final `commit()`, too late). If opening trace fails, everything degrades to prior batch mode.
"""
from __future__ import annotations

import contextvars
import logging
import uuid
from contextlib import contextmanager
from typing import Any, Iterator
from ..settings import settings

log = logging.getLogger(__name__)

# Observability failure can't break store: warn once, then go blind.
_warned = False

# D.3 — `True` while request has LIVE trace open (`start_trace` succeeded). That's what
# `callbacks()` reads to set callback in "hang on existing trace" mode instead of batch mode
# (which only materializes spans on final `commit()`). ContextVar because scope is request.
_live_trace_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "galileo_live_trace", default=False,
)

# Trace name when caller doesn't give feature (run_demo, simulator, internal paths).
_DEFAULT_TRACE_NAME = "vega.request"

# Core evaluator set from workshop module 6 — **member names** of the SDK's `GalileoMetrics` enum
# (`galileo/schema/metrics.py`), resolved lazily in `ensure_stream_metrics` so `galileo` stays an
# optional import. The `_luna` suffix is the SLM variant; a name with no `_luna` member (e.g.
# `agent_efficiency`, `instruction_adherence`) only exists as LLM. The judge model a PRESET runs on
# (Bedrock, GPT, …) is Console configuration, not part of its name — a scorer whose name mentions a
# model is a custom one, and those go in `WORKSHOP_CUSTOM_METRICS` below.
# List the valid names with:
#   cd backend && .venv/bin/python -c "from galileo.schema.metrics import GalileoMetrics; \
#     [print(f'{m.name:36} {m.value}') for m in GalileoMetrics]"
WORKSHOP_METRICS: tuple[str, ...] = (
    "context_adherence_luna",  # UC-1 — grounded in retrieved content?
    "agent_efficiency",        # UC-2 preset — redundant steps / token waste?
    "correctness",             # UC-3 — answer factually right?
    "instruction_adherence",   # UC-3 — policy followed?
    "prompt_injection_luna",   # UC-4 — override accepted?
    "input_pii_luna",          # UC-5 — sensitive data in input?
    "output_pii_luna",         # UC-5 — sensitive data in output?
)

# Custom scorers (built in Console, not in the SDK enum) — matched by their **exact Console label**.
# They only exist in the org that created them, so an unknown one is skipped with a warning instead
# of taking the preset evaluators down with it. List what this org has with:
#   from galileo.scorers import Scorers; from galileo.resources.models.scorer_types import ScorerTypes
#   [s.label for s in Scorers().list(types=list(ScorerTypes)) if s.scorer_type != "preset"]
WORKSHOP_CUSTOM_METRICS: tuple[str, ...] = (
    "Correctness AWS Bedrock",  # UC-3 — Correctness judged by the Bedrock-hosted model
)


def is_enabled() -> bool:
    return bool(settings.galileo_api_key.strip())


def project() -> str:
    return settings.galileo_project


def log_stream() -> str:
    """`GALILEO_LOG_STREAM` is name SDK/console uses; `GALILEO_LOGSTREAM` accepted because
    it's what `.env.example` had before this stage."""
    return settings.galileo_log_stream


def console_url() -> str:
    return settings.galileo_console_url.strip()


def agent_control_url() -> str:
    """Agent Control URL — derived from console when not explicit.

    Single source: `galileo_control.init_once` consumes this same function (F-BACKEND-1).
    """
    explicit = settings.agent_control_url.strip()
    if explicit:
        return explicit
    console = console_url()
    if "console." in console:
        return console.replace("console.", "agent-control.", 1)
    return "https://agent-control.multitenant.galileocloud.io"


def session_idle_minutes() -> int:
    """Minutes without AI request before front rotates Splunk Agent Observability session (F-GALILEO-8).
    `0` = rotate manually only (BTS button). Default 5."""
    value = settings.vega_session_idle_minutes
    if value < 0:
        return 0
    return min(value, 1440)


def shopper_session_name(active_scenario: str | None = None) -> str | None:
    """Console session label when a workshop UC preset is active — e.g. uc-1 → Session-UC1."""
    from ..problems import FLAGS

    scenario = (active_scenario if active_scenario is not None else FLAGS.active_scenario or "").strip()
    if not scenario:
        return None
    if scenario.startswith("uc-"):
        return f"Session-UC{scenario[3:]}"
    return f"Session-{scenario.upper()}"


def public_config() -> dict:
    """Public metadata (no API key) — precedent: `rum.public_config()`."""
    return {
        "enabled": is_enabled(),
        "console_url": console_url(),
        "project": project(),
        "log_stream": log_stream(),
        "agent_control_url": agent_control_url(),
        "session_idle_minutes": session_idle_minutes(),
    }


def _known_custom_scorer_labels() -> set[str] | None:
    """Labels/names of this org's non-preset scorers — `None` when they can't be listed.

    `Scorers().list()` with no filter returns ONLY presets, so the custom ones (the whole point
    of this lookup) need the explicit type list."""
    try:
        from galileo.resources.models.scorer_types import ScorerTypes
        from galileo.scorers import Scorers

        labels: set[str] = set()
        for scorer in Scorers().list(types=list(ScorerTypes)):
            for value in (scorer.name, getattr(scorer, "label", None)):
                if isinstance(value, str) and value:
                    labels.add(value)
        return labels
    except Exception as exc:  # noqa: BLE001
        log.debug("galileo: scorer catalog unreadable (%s: %s)", type(exc).__name__, exc)
        return None


def preset_scorer_ref(name: str) -> tuple[str, str, str] | None:
    """`(scorer_id, scorer_version_id, label)` of the preset `name` — `None` when unresolvable.

    An Agent Control control that reads a metric's value carries those two UUIDs in its condition;
    they're per-Console identifiers, so they're looked up here instead of hardcoded. Lives in this
    module because it's the one that already talks to the scorer catalog (`galileo_control` stays
    the only module importing `agent_control`)."""
    try:
        from galileo.resources.models.scorer_types import ScorerTypes
        from galileo.scorers import Scorers

        for scorer in Scorers().list(types=list(ScorerTypes)):
            if getattr(scorer, "name", None) != name:
                continue
            version = getattr(scorer, "default_version", None)
            version_id = getattr(version, "id", None)
            if not version_id:
                return None
            label = getattr(scorer, "label", None) or name
            return str(scorer.id), str(version_id), str(label)
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("galileo: preset scorer %r unresolvable (%s: %s)", name, type(exc).__name__, exc)
        return None


def _resolve_workshop_metrics() -> list:
    """The configured evaluators as `enable_metrics` inputs, skipping (loudly) unknown ones.

    Presets resolve to `GalileoMetrics` members; custom scorers stay as plain strings, which the
    SDK matches by Console label. Both are checked one by one on purpose: resolving the whole
    list in a single expression let a SINGLE bad name (`correctness_aws_bedrock`) raise and take
    down the entire call — the stream got created and then NOTHING was enabled on it."""
    from galileo.schema.metrics import GalileoMetrics

    resolved: list = []
    unknown: list[str] = []
    for name in WORKSHOP_METRICS:
        try:
            resolved.append(GalileoMetrics[name])
        except KeyError:
            unknown.append(name)
    if unknown:
        log.error(
            "galileo: unknown evaluator name(s) in WORKSHOP_METRICS, ignored: %s "
            "(valid names are the GalileoMetrics enum members)", ", ".join(unknown),
        )

    if WORKSHOP_CUSTOM_METRICS:
        # `None` = catalog unreadable; pass the labels through and let the write attempt decide.
        known = _known_custom_scorer_labels()
        missing = [] if known is None else [c for c in WORKSHOP_CUSTOM_METRICS if c not in known]
        resolved.extend(c for c in WORKSHOP_CUSTOM_METRICS if c not in missing)
        if missing:
            log.error(
                "galileo: custom scorer(s) not found in this Console, ignored: %s "
                "(match is by exact label)", ", ".join(missing),
            )
    return resolved


def _configured_scorer_count(project_id: str, stream_id: str) -> int | None:
    """How many scorers the stream already has — `None` when it can't be determined.

    `enable_metrics` is an upsert of the stream's whole scorer config, so it can only run when
    the stream has none: that's what makes "never clobber what the owner set in Console" and
    "a stream left empty still gets its evaluators" hold at the same time."""
    try:
        from galileo.config import GalileoPythonConfig
        from galileo.resources.api.log_stream import (
            get_metric_settings_projects_project_id_log_streams_log_stream_id_metric_settings_get as get_settings,
        )

        response = get_settings.sync(
            client=GalileoPythonConfig.get().api_client,
            project_id=project_id, log_stream_id=stream_id,
        )
        # The endpoint RETURNS `HTTPValidationError` (or `None`) instead of raising, and neither
        # carries `scorers`. Counting those as 0 would read "empty" and let the backfill upsert
        # over a stream that in fact has a config. A genuinely empty stream answers with `[]`,
        # so only a real list is an answer; anything else is "unknown".
        scorers = getattr(response, "scorers", None)
        return len(scorers) if isinstance(scorers, list) else None
    except Exception as exc:  # noqa: BLE001 — private-ish endpoint; treat as "unknown"
        log.debug("galileo: scorer settings unreadable (%s: %s)", type(exc).__name__, exc)
        return None


def ensure_stream_metrics() -> None:
    """Gives the log stream the workshop evaluators — boot-time, idempotent, best effort.

    Each workshop VM boots with its own `GALILEO_LOG_STREAM`; without this, the stream is
    lazily created by the SDK on the first trace with NO evaluators, and every attendee has to
    enable them by hand in Console. Applies `WORKSHOP_METRICS` when the stream is created here,
    and also when an existing stream has **no scorers at all** — a stream that already carries a
    config (whatever the owner picked in Console) is never touched. Any failure just logs;
    Galileo being unreachable can't block the store's boot."""
    if not is_enabled():
        return
    try:
        from galileo.log_streams import create_log_stream, get_log_stream
        from galileo.projects import create_project, get_project

        # Resolve BEFORE any write: a typo must not leave a freshly created stream evaluator-less.
        metrics = _resolve_workshop_metrics()
        if not metrics:
            log.error("galileo: no valid evaluator in WORKSHOP_METRICS — stream left as is")
            return

        if get_project(name=project()) is None:
            create_project(project())
        stream = get_log_stream(name=log_stream(), project_name=project())
        if stream is None:
            stream = create_log_stream(name=log_stream(), project_name=project())
            action = "created"
        elif _configured_scorer_count(str(stream.project_id), str(stream.id)) == 0:
            action = "backfilled"  # stream exists with an empty config (nothing to clobber)
        else:
            return
        # All of WORKSHOP_METRICS are server-side scorers, so the returned list of
        # client-side (local) metric configs is empty — nothing to process here.
        stream.enable_metrics(metrics)
        log.info(
            "galileo: log stream %r %s in project %r with %d evaluators (%s)",
            log_stream(), action, project(), len(metrics),
            ", ".join(str(getattr(m, "value", m)) for m in metrics),  # enum members + custom labels
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("galileo: ensure_stream_metrics skipped (%s: %s)", type(exc).__name__, exc)


def _warn_once(exc: Exception) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("Splunk Agent Observability disabled for this run (%s: %s)", type(exc).__name__, exc)


def new_session_id(candidate: str | None = None) -> str:
    """Valid UUID to group the session. Splunk Agent Observability requires a UUID in `external_id`, so a header
    that's missing or malformed is replaced with a new one instead of rejecting the request."""
    if candidate:
        try:
            return str(uuid.UUID(candidate.strip()))
        except (ValueError, AttributeError):
            pass
    return str(uuid.uuid4())


def live_trace_active() -> bool:
    """`True` when this request's `session_scope` managed to open the live trace (D.3)."""
    return _live_trace_var.get()


def callbacks() -> list:
    """`[VegaGalileoCallback()]` when enabled, `[]` otherwise. One per request."""
    if not is_enabled():
        return []
    try:
        from .galileo_callback import VegaGalileoCallback

        if live_trace_active():
            # Trace already opened by `session_scope`: the callback hangs the tree on the live parent.
            # `start_new_trace=False` avoids a SECOND (concurrent) trace on `commit()`; and
            # `flush_on_chain_end=False` leaves the flush to `session_scope`'s `finally` — flushing
            # mid-request zeroes out `current_parent()` and would kill the `[control]` of any
            # later evaluation (and the root's own `conclude`).
            return [VegaGalileoCallback(start_new_trace=False, flush_on_chain_end=False)]
        return [VegaGalileoCallback()]
    except Exception as exc:  # noqa: BLE001 — package missing or SDK without valid credential
        _warn_once(exc)
        return []


def _logger_instance() -> Any:
    """Current Splunk Agent Observability context logger — the SAME ONE the callback uses
    (`GalileoBaseHandler` also comes from `galileo_context.get_logger_instance()`)."""
    from galileo import galileo_context

    return galileo_context.get_logger_instance()


def _enable_agent_control_bridge(logger_instance: Any) -> None:
    """Ensures the Agent Control → Galileo bridge on the request's logger (safety net).

    `galileo_control.init_once()` configures the SDK with `observability_sink_name="registered"`, and the
    only sink that answers to that name is `GalileoAgentControlBridge`, created by
    `logger.enable_agent_control()`. In `galileo==2.6.0` `GalileoLogger.__init__` itself already does
    this (`_auto_enable_agent_control_if_available`) — but that's pinned-version behavior, and without
    the bridge there's NO `[control]` span. The call is idempotent (the bridge's `register()` returns
    immediately if already registered), so repeating it costs nothing and covers the reverse order."""
    try:
        from . import galileo_control  # late import: galileo_control imports this module

        if not galileo_control.is_active():
            return
        logger_instance.enable_agent_control()
    except Exception as exc:  # noqa: BLE001 — without the bridge the trace continues, just without `[control]`
        log.debug("Agent Control bridge unavailable (%s: %s)", type(exc).__name__, exc)


def _start_live_trace(feature: str | None, session_id: str) -> Any | None:
    """Opens the request trace BEFORE processing and returns the logger — `None` = batch mode.

    Why live: `GalileoAgentControlBridge` only converts an Agent Control event when the
    logger has an active `current_parent()`, and in batch mode the callback only materializes spans
    on the final `commit()` — by which point control evaluations have already happened. With the trace
    open here, the parent exists throughout the whole request and `[control]` enters as a child of the root.

    Degradation: any failure returns `None` and the request continues in the pre-D.3 batch mode
    (the callback goes back to opening/concluding/flushing the trace on its own at `commit()`)."""
    try:
        logger_instance = _logger_instance()
        if logger_instance.current_parent() is not None:
            # A trace is already open in this context (unexpected nesting): opening another would make
            # the SDK raise. Better to let whoever opened it conclude it.
            return None
        name = (feature or "").strip() or _DEFAULT_TRACE_NAME
        trace = logger_instance.start_trace(
            input="",  # filled in by the callback with the compact LangGraph workflow payload
            name=name,
            metadata={"feature": name, "session_id": session_id},
        )
        if trace is None:
            # The SDK swallows infra exceptions inside `start_trace` and returns `None`.
            return None
        _enable_agent_control_bridge(logger_instance)
        return logger_instance
    except Exception as exc:  # noqa: BLE001
        _warn_once(exc)
        _abandon_live_trace()
        return None


def _abandon_live_trace() -> None:
    """Undoes a half-open trace so batch mode (fallback) can open its own."""
    try:
        logger_instance = _logger_instance()
        if logger_instance.current_parent() is not None:
            logger_instance.conclude(conclude_all=True)
    except Exception:  # noqa: BLE001 — best effort; nothing here can escape to the request
        pass


def _conclude_live_trace(logger_instance: Any) -> None:
    """Closes and flushes the request trace. Nothing here can escape to the request."""
    trace = None
    try:
        trace = logger_instance.current_parent()
        # Without an explicit `output` the SDK inherits it from the last child — which is the LangGraph
        # workflow, already compacted (`compact_trace_payload`). `conclude_all` closes any span left
        # open by an error partway through the graph.
        logger_instance.conclude(conclude_all=True)
        if trace is not None and not getattr(trace, "spans", None):
            # Request that produced no spans at all (cache, short-circuit): an empty trace is just noise
            # in the Console — in batch mode it wouldn't even exist ("No nodes to commit").
            try:
                logger_instance.traces.remove(trace)
            except (ValueError, AttributeError):
                pass
    except Exception as exc:  # noqa: BLE001
        _warn_once(exc)
    try:
        logger_instance.flush()
    except Exception as exc:  # noqa: BLE001
        _warn_once(exc)


@contextmanager
def session_scope(session_id: str | None = None, *, feature: str | None = None) -> Iterator[str | None]:
    """Opens the Splunk Agent Observability context (project + log stream), the shopper journey
    session, and the request's **live trace** (D.3).

    `session_id` comes from the `X-Vega-Session` header (a UUID persisted in the front end's
    `localStorage`), which stitches multiple requests into a single session — that's what enables
    the Console's session-node metrics. `feature` becomes the trace root's name. No-op when
    Splunk Agent Observability is disabled."""
    if not is_enabled():
        yield None
        return
    try:
        from galileo import galileo_context
    except Exception as exc:  # noqa: BLE001
        _warn_once(exc)
        yield None
        return
    resolved = new_session_id(session_id)
    ctx = galileo_context(project=project(), log_stream=log_stream())
    try:
        ctx.__enter__()
    except Exception as exc:  # noqa: BLE001 — console unreachable / invalid credential
        _warn_once(exc)
        yield None
        return
    live_logger = None
    live_token = None
    try:
        try:
            session_kwargs: dict[str, str] = {"external_id": resolved}
            session_name = shopper_session_name()
            if session_name:
                session_kwargs["name"] = session_name
            galileo_context.start_session(**session_kwargs)
        except Exception as exc:  # noqa: BLE001 — session is enrichment, not a requirement
            _warn_once(exc)
        # Live trace AFTER the session (the session needs to be set when the trace is ingested).
        live_logger = _start_live_trace(feature, resolved)
        if live_logger is not None:
            live_token = _live_trace_var.set(True)
        yield resolved
    finally:
        if live_token is not None:
            _live_trace_var.reset(live_token)
        if live_logger is not None:
            _conclude_live_trace(live_logger)
        # The SDK's `__exit__` flushes the spans. If the network drops right here, the store
        # can't return a 500 because of it — that's why the CM is entered/exited by hand instead of a
        # `with`: a `with` would let the flush failure escape to the request.
        try:
            ctx.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            _warn_once(exc)
