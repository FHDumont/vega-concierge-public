"""Central RunnableConfig — thread_id, metadata and observability callbacks (F-OBS-PREP-5/7).

The `callbacks` hook that readiness left reserved is filled in F-GALILEO-1 (ADR-032):
`galileo_obs.callbacks()` returns `[GalileoAsyncCallback()]` when there's a credential and `[]` when
not — the only backend point that knows about the existence of Splunk Agent Observability.
"""
from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from typing import Iterator

from langchain_core.runnables.config import RunnableConfig

from .obs import galileo_obs
from .problems import FLAGS
from .settings import settings

_current_runnable_config: contextvars.ContextVar[RunnableConfig | None] = contextvars.ContextVar(
    "current_runnable_config", default=None
)


def _env() -> str:
    return settings.deployment_environment


def make_thread_id(*, user_id: str | None = None) -> str:
    """Workshop session: `{env}:{user|anon}:{uuid12}`."""
    who = user_id or "anon"
    return f"{_env()}:{who}:{uuid.uuid4().hex[:12]}"


def build_runnable_config(
    *,
    thread_id: str,
    feature: str,
    metadata: dict | None = None,
) -> RunnableConfig:
    """Builds RunnableConfig with merged metadata and observability callbacks of the moment.

    One NEW callback per config (= per request): this is how each user turn becomes its own trace
    in the Console, instead of everything falling into one giant trace."""
    merged: dict = {
        "feature": feature,
        "vm": _env(),
        "problem_flags": FLAGS.to_dict(),
    }
    if metadata:
        merged.update(metadata)
    return RunnableConfig(
        configurable={"thread_id": thread_id},
        metadata=merged,
        tags=[],
        callbacks=galileo_obs.callbacks(),
    )


def set_current_runnable_config(
    config: RunnableConfig | None,
    token: contextvars.Token | None = None,
) -> contextvars.Token | None:
    """Publishes or clears config in contextvar. With `token`, resets; otherwise sets and returns token."""
    if token is not None:
        _current_runnable_config.reset(token)
        return None
    return _current_runnable_config.set(config)


def current_runnable_config() -> RunnableConfig | None:
    """Config published by current request, or None. Unlike `resolve_config`, does NOT create a new one —
    someone who just wants to hang on the existing trace shouldn't fabricate an orphaned trace."""
    return _current_runnable_config.get()


@contextmanager
def bind_runnable_config(config: RunnableConfig) -> Iterator[RunnableConfig]:
    """Context manager: exposes config to contextvar (features/nested LLM in same request)."""
    token = _current_runnable_config.set(config)
    try:
        yield config
    finally:
        _current_runnable_config.reset(token)


@contextmanager
def ai_request_scope(
    *,
    feature: str,
    session_id: str | None = None,
    user_id: str | None = None,
    metadata: dict | None = None,
) -> Iterator[RunnableConfig]:
    """Scope of ONE AI request: Splunk Agent Observability session + config published in contextvar.

    Combines the three steps every AI endpoint needs (open the journey session, build the
    config with callbacks, publish it to the synchronous path of features) into one CM. Config
    is created INSIDE the session, otherwise the callback is born outside the context and the trace becomes orphaned —
    and, since D.3, also outside the live trace: it's `session_scope` that decides if the callback
    is born in batch mode or hanging on the request trace."""
    with galileo_obs.session_scope(session_id, feature=feature) as resolved_session:
        extra = dict(metadata or {})
        if user_id is not None:
            extra["user_id"] = user_id
        if resolved_session is not None:
            extra["session_id"] = resolved_session
        config = build_runnable_config(
            thread_id=make_thread_id(user_id=user_id),
            feature=feature,
            metadata=extra or None,
        )
        with bind_runnable_config(config):
            yield config


def derive_feature_config(parent: RunnableConfig | None, feature: str) -> RunnableConfig:
    """Derives child RunnableConfig with specialist's `metadata.feature`.

    Inherits `configurable` (thread_id), `callbacks`, `tags` and other metadata from parent;
    always replaces `feature`. If parent had `feature=chat`, records `parent_feature=chat`
    (breadcrumb for Console filters). One trace per turn — doesn't create second callback."""
    if parent is None:
        return build_runnable_config(thread_id=make_thread_id(), feature=feature)
    parent_meta = dict(parent.get("metadata") or {})
    child_meta = {**parent_meta, "feature": feature}
    if parent_meta.get("feature") == "chat":
        child_meta["parent_feature"] = "chat"
    return RunnableConfig(
        configurable=dict(parent.get("configurable") or {}),
        metadata=child_meta,
        tags=list(parent.get("tags") or []),
        callbacks=parent.get("callbacks"),
    )


def resolve_config(
    config: RunnableConfig | None,
    *,
    feature: str,
    user_id: str | None = None,
    order_id: str | None = None,
) -> RunnableConfig:
    """Uses explicit config, contextvar or sensible default (run_demo/simulator)."""
    if config is not None:
        return config
    ctx = _current_runnable_config.get()
    if ctx is not None:
        return ctx
    extra: dict = {}
    if user_id is not None:
        extra["user_id"] = user_id
    if order_id is not None:
        extra["order_id"] = order_id
    return build_runnable_config(
        thread_id=make_thread_id(user_id=user_id),
        feature=feature,
        metadata=extra or None,
    )
