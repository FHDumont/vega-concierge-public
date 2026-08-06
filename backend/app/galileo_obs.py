"""Integração Splunk Agent Observability (ADR-032) — opt-in pela presença de `GALILEO_API_KEY`.

Padrão do `healthcare-assistant/2-app-with-instrumentation`: `galileo_context(project, log_stream)`
+ `start_session(external_id=...)` por request, e **um `GalileoAsyncCallback` por request** (cada
turno do usuário vira um trace próprio). Nada de decorator `@log` nem de wrapper `galileo.openai`.

Sem `GALILEO_API_KEY` — ou sem o pacote `galileo` instalado — tudo aqui é no-op e a app roda
idêntica ao comportamento anterior: é a demo "base" do workshop. O import do SDK é lazy e guardado
justamente pra que `requirements.txt` não vire pré-requisito de subir a loja.

Os **evaluators são configurados no Console** (no Log stream), não aqui. Nenhum nome de métrica
Splunk Agent Observability aparece no código do Vega.

Rótulos legíveis de span (LLM `name=`, nós ReAct, `run_name` em chains) vivem em `galileo_span.py`;
este módulo só entrega o callback e o contexto de sessão.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from typing import Iterator
from .settings import settings

log = logging.getLogger(__name__)

# Falha de observabilidade não pode derrubar a loja: avisamos uma vez e seguimos cegos.
_warned = False


def is_enabled() -> bool:
    return bool(settings.galileo_api_key.strip())


def project() -> str:
    return settings.galileo_project


def log_stream() -> str:
    """`GALILEO_LOG_STREAM` é o nome que o SDK/console usa; `GALILEO_LOGSTREAM` é aceito porque
    é o que o `.env.example` trazia antes desta fase."""
    return settings.galileo_log_stream


def console_url() -> str:
    return settings.galileo_console_url.strip()


def agent_control_url() -> str:
    """URL do Agent Control — derivada do console quando não vier explícita.

    Fonte única: `galileo_control.init_once` consome esta mesma função (F-BACKEND-1).
    """
    explicit = settings.agent_control_url.strip()
    if explicit:
        return explicit
    console = console_url()
    if "console." in console:
        return console.replace("console.", "agent-control.", 1)
    return "https://agent-control.multitenant.galileocloud.io"


def session_idle_minutes() -> int:
    """Minutos sem request de IA antes do front rotacionar a session Splunk Agent Observability (F-GALILEO-8).
    `0` = só rotaciona manualmente (botão BTS). Default 5."""
    value = settings.vega_session_idle_minutes
    if value < 0:
        return 0
    return min(value, 1440)


def public_config() -> dict:
    """Metadados públicos (sem API key) — precedente: `rum.public_config()`."""
    return {
        "enabled": is_enabled(),
        "console_url": console_url(),
        "project": project(),
        "log_stream": log_stream(),
        "agent_control_url": agent_control_url(),
        "session_idle_minutes": session_idle_minutes(),
    }


def _warn_once(exc: Exception) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("Splunk Agent Observability desabilitado nesta execução (%s: %s)", type(exc).__name__, exc)


def new_session_id(candidate: str | None = None) -> str:
    """UUID válido para agrupar a sessão. O Splunk Agent Observability exige UUID no `external_id`, então um header
    ausente ou malformado é substituído por um novo em vez de rejeitar a request."""
    if candidate:
        try:
            return str(uuid.UUID(candidate.strip()))
        except (ValueError, AttributeError):
            pass
    return str(uuid.uuid4())


def callbacks() -> list:
    """`[VegaGalileoCallback()]` quando habilitado, `[]` caso contrário. Um por request."""
    if not is_enabled():
        return []
    try:
        from .galileo_callback import VegaGalileoCallback

        return [VegaGalileoCallback()]
    except Exception as exc:  # noqa: BLE001 — pacote ausente ou SDK sem credencial válida
        _warn_once(exc)
        return []


@contextmanager
def session_scope(session_id: str | None = None) -> Iterator[str | None]:
    """Abre o contexto Splunk Agent Observability (projeto + log stream) e a sessão da jornada do comprador.

    O `session_id` vem do header `X-Vega-Session` (UUID persistido no `localStorage` do front), o
    que costura vários requests numa sessão só — é o que habilita as métricas de nó de sessão do
    Console. No-op quando o Splunk Agent Observability está desligado."""
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
    except Exception as exc:  # noqa: BLE001 — console inalcançável / credencial inválida
        _warn_once(exc)
        yield None
        return
    try:
        try:
            galileo_context.start_session(external_id=resolved)
        except Exception as exc:  # noqa: BLE001 — sessão é enriquecimento, não requisito
            _warn_once(exc)
        yield resolved
    finally:
        # O `__exit__` do SDK faz o flush dos spans. Se a rede cair exatamente aqui, a loja não
        # pode devolver 500 por causa disso — por isso o CM é entrado/saído à mão, em vez de um
        # `with`: um `with` deixaria a falha de flush escapar pro request.
        try:
            ctx.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            _warn_once(exc)
