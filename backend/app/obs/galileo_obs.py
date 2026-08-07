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
este módulo entrega o callback, o contexto de sessão e — desde a F-BACKEND-3 (D.3) — o **trace
vivo** do request: `start_trace` logo depois do `start_session`, `conclude`+flush no `finally`. É
o trace aberto que dá um `current_parent()` ao SDK durante todo o request, sem o qual o Agent
Control não tem onde pendurar o span `[control]` (o modo batch do callback só materializa spans
no `commit()` final, tarde demais). Se abrir o trace falhar, tudo degrada pro modo batch anterior.
"""
from __future__ import annotations

import contextvars
import logging
import uuid
from contextlib import contextmanager
from typing import Any, Iterator
from ..settings import settings

log = logging.getLogger(__name__)

# Falha de observabilidade não pode derrubar a loja: avisamos uma vez e seguimos cegos.
_warned = False

# D.3 — `True` enquanto o request tem um trace VIVO aberto (`start_trace` deu certo). É o que o
# `callbacks()` lê pra configurar o callback em modo "pendura no trace existente" em vez do modo
# batch (que só materializa spans no `commit()` final). ContextVar porque o escopo é o request.
_live_trace_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "galileo_live_trace", default=False,
)

# Nome do trace quando o chamador não informa a feature (run_demo, simulador, caminhos internos).
_DEFAULT_TRACE_NAME = "vega.request"


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


def live_trace_active() -> bool:
    """`True` quando o `session_scope` deste request conseguiu abrir o trace vivo (D.3)."""
    return _live_trace_var.get()


def callbacks() -> list:
    """`[VegaGalileoCallback()]` quando habilitado, `[]` caso contrário. Um por request."""
    if not is_enabled():
        return []
    try:
        from .galileo_callback import VegaGalileoCallback

        if live_trace_active():
            # Trace já aberto pelo `session_scope`: o callback pendura a árvore no parent vivo.
            # `start_new_trace=False` evita um SEGUNDO trace (concorrente) no `commit()`; e
            # `flush_on_chain_end=False` deixa o flush pro `finally` do `session_scope` — flush
            # no meio do request zera o `current_parent()` e mataria o `[control]` de qualquer
            # avaliação posterior (e o próprio `conclude` da raiz).
            return [VegaGalileoCallback(start_new_trace=False, flush_on_chain_end=False)]
        return [VegaGalileoCallback()]
    except Exception as exc:  # noqa: BLE001 — pacote ausente ou SDK sem credencial válida
        _warn_once(exc)
        return []


def _logger_instance() -> Any:
    """Logger do contexto Splunk Agent Observability corrente — o MESMO que o callback usa
    (`GalileoBaseHandler` também sai de `galileo_context.get_logger_instance()`)."""
    from galileo import galileo_context

    return galileo_context.get_logger_instance()


def _enable_agent_control_bridge(logger_instance: Any) -> None:
    """Garante o bridge Agent Control → Galileo no logger do request (rede de segurança).

    `galileo_control.init_once()` configura o SDK com `observability_sink_name="registered"`, e o
    único sink que atende por esse nome é o `GalileoAgentControlBridge`, criado por
    `logger.enable_agent_control()`. No `galileo==2.6.0` o próprio `GalileoLogger.__init__` já faz
    isso (`_auto_enable_agent_control_if_available`) — mas é comportamento de versão pinada, e sem
    o bridge NÃO existe span `[control]`. A chamada é idempotente (o `register()` do bridge volta
    na hora se já estiver registrado), então repetir custa nada e cobre a ordem inversa."""
    try:
        from . import galileo_control  # import tardio: galileo_control importa este módulo

        if not galileo_control.is_active():
            return
        logger_instance.enable_agent_control()
    except Exception as exc:  # noqa: BLE001 — sem bridge o trace segue, só sem `[control]`
        log.debug("Agent Control bridge indisponível (%s: %s)", type(exc).__name__, exc)


def _start_live_trace(feature: str | None, session_id: str) -> Any | None:
    """Abre o trace do request ANTES do processamento e devolve o logger — `None` = modo batch.

    Por que vivo: o `GalileoAgentControlBridge` só converte evento de Agent Control quando o
    logger tem um `current_parent()` ativo, e no modo batch o callback só materializa spans no
    `commit()` final — quando as avaliações de controle já passaram. Com o trace aberto aqui, o
    parent existe durante todo o request e o `[control]` entra como filho da raiz.

    Degradação: qualquer falha devolve `None` e o request segue no modo batch de antes da D.3
    (o callback volta a abrir/concluir/flushar o trace sozinho no `commit()`)."""
    try:
        logger_instance = _logger_instance()
        if logger_instance.current_parent() is not None:
            # Já existe trace aberto neste contexto (aninhamento inesperado): abrir outro faria
            # o SDK levantar. Melhor deixar quem abriu concluir.
            return None
        name = (feature or "").strip() or _DEFAULT_TRACE_NAME
        trace = logger_instance.start_trace(
            input="",  # preenchido pelo callback com o payload compacto do workflow LangGraph
            name=name,
            metadata={"feature": name, "session_id": session_id},
        )
        if trace is None:
            # O SDK engole exceção de infra dentro do `start_trace` e devolve `None`.
            return None
        _enable_agent_control_bridge(logger_instance)
        return logger_instance
    except Exception as exc:  # noqa: BLE001
        _warn_once(exc)
        _abandon_live_trace()
        return None


def _abandon_live_trace() -> None:
    """Desfaz um trace meio-aberto pra que o modo batch (fallback) possa abrir o dele."""
    try:
        logger_instance = _logger_instance()
        if logger_instance.current_parent() is not None:
            logger_instance.conclude(conclude_all=True)
    except Exception:  # noqa: BLE001 — melhor esforço; nada aqui pode escapar pro request
        pass


def _conclude_live_trace(logger_instance: Any) -> None:
    """Fecha e sobe o trace do request. Nada aqui pode escapar pro request."""
    trace = None
    try:
        trace = logger_instance.current_parent()
        # Sem `output` explícito o SDK herda o do último filho — que é o workflow LangGraph já
        # compactado (`compact_trace_payload`). `conclude_all` fecha qualquer span que tenha
        # ficado aberto por um erro no meio do grafo.
        logger_instance.conclude(conclude_all=True)
        if trace is not None and not getattr(trace, "spans", None):
            # Request que não gerou span nenhum (cache, curto-circuito): trace vazio é só ruído
            # no Console — no modo batch ele nem existiria ("No nodes to commit").
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
    """Abre o contexto Splunk Agent Observability (projeto + log stream), a sessão da jornada do
    comprador e o **trace vivo** do request (D.3).

    O `session_id` vem do header `X-Vega-Session` (UUID persistido no `localStorage` do front), o
    que costura vários requests numa sessão só — é o que habilita as métricas de nó de sessão do
    Console. O `feature` vira o nome da raiz do trace. No-op quando o Splunk Agent Observability
    está desligado."""
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
    live_logger = None
    live_token = None
    try:
        try:
            session_kwargs: dict[str, str] = {"external_id": resolved}
            session_name = shopper_session_name()
            if session_name:
                session_kwargs["name"] = session_name
            galileo_context.start_session(**session_kwargs)
        except Exception as exc:  # noqa: BLE001 — sessão é enriquecimento, não requisito
            _warn_once(exc)
        # Trace vivo DEPOIS da sessão (a sessão precisa estar setada quando o trace é ingerido).
        live_logger = _start_live_trace(feature, resolved)
        if live_logger is not None:
            live_token = _live_trace_var.set(True)
        yield resolved
    finally:
        if live_token is not None:
            _live_trace_var.reset(live_token)
        if live_logger is not None:
            _conclude_live_trace(live_logger)
        # O `__exit__` do SDK faz o flush dos spans. Se a rede cair exatamente aqui, a loja não
        # pode devolver 500 por causa disso — por isso o CM é entrado/saído à mão, em vez de um
        # `with`: um `with` deixaria a falha de flush escapar pro request.
        try:
            ctx.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            _warn_once(exc)
