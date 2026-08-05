"""RunnableConfig central — thread_id, metadata e callbacks de observabilidade (F-OBS-PREP-5/7).

O gancho `callbacks` que a readiness deixou reservado é preenchido na F-GALILEO-1 (ADR-032):
`galileo_obs.callbacks()` devolve `[GalileoAsyncCallback()]` quando há credencial e `[]` quando
não — o único ponto do backend que sabe da existência do Splunk Agent Observability.
"""
from __future__ import annotations

import contextvars
import os
import uuid
from contextlib import contextmanager
from typing import Iterator

from langchain_core.runnables.config import RunnableConfig

from . import galileo_obs
from .problems import FLAGS

_current_runnable_config: contextvars.ContextVar[RunnableConfig | None] = contextvars.ContextVar(
    "current_runnable_config", default=None
)


def _env() -> str:
    return os.getenv("DEPLOYMENT_ENVIRONMENT", "local-dev")


def make_thread_id(*, user_id: str | None = None) -> str:
    """Session de workshop: `{env}:{user|anon}:{uuid12}`."""
    who = user_id or "anon"
    return f"{_env()}:{who}:{uuid.uuid4().hex[:12]}"


def build_runnable_config(
    *,
    thread_id: str,
    feature: str,
    metadata: dict | None = None,
) -> RunnableConfig:
    """Monta RunnableConfig com metadata mergeada e os callbacks de observabilidade do momento.

    Um callback NOVO por config (= por request): é assim que cada turno do usuário vira um trace
    próprio no Console, em vez de tudo cair num trace gigante."""
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
    """Publica ou limpa o config no contextvar. Com `token`, faz reset; senão set e devolve token."""
    if token is not None:
        _current_runnable_config.reset(token)
        return None
    return _current_runnable_config.set(config)


def current_runnable_config() -> RunnableConfig | None:
    """Config publicado pelo request corrente, ou None. Diferente de `resolve_config`, NÃO cria um
    novo — quem só quer se pendurar no trace existente não deve fabricar um trace órfão."""
    return _current_runnable_config.get()


@contextmanager
def bind_runnable_config(config: RunnableConfig) -> Iterator[RunnableConfig]:
    """Context manager: expõe config ao contextvar (features/nested LLM no mesmo request)."""
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
    """Escopo de UM request de IA: sessão Splunk Agent Observability + config publicado no contextvar.

    Junta os três passos que todo endpoint de IA precisa (abrir a sessão da jornada, montar o
    config com os callbacks, publicá-lo pro caminho síncrono das features) num CM só. O config
    é criado DENTRO da sessão, senão o callback nasce fora do contexto e o trace fica órfão."""
    with galileo_obs.session_scope(session_id) as resolved_session:
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
    """Deriva RunnableConfig filho com `metadata.feature` do specialist.

    Herda `configurable` (thread_id), `callbacks`, `tags` e demais metadata do pai;
    substitui sempre `feature`. Se o pai tinha `feature=chat`, grava `parent_feature=chat`
    (breadcrumb p/ filtros no Console). Um trace por turno — não cria segundo callback."""
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
    """Usa config explícito, contextvar ou default sensato (run_demo/simulador)."""
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
