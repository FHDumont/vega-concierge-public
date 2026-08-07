"""Trace vivo por request (F-BACKEND-3, Etapa D.3).

O que está sob teste é o **ciclo de vida**: `session_scope` abre um trace de verdade antes do
processamento (é o `current_parent()` vivo que faz o `GalileoAgentControlBridge` aceitar os
eventos e o span `[control]` existir), configura o callback pra pendurar a árvore nele, e fecha
tudo no `finally`.

A regra que mais importa aqui é a **degradação**: se qualquer coisa do SDK falhar, o request tem
que continuar e o trace volta ao modo batch de antes desta etapa. Observabilidade não derruba a
loja — por isso metade dos testes abaixo é caminho de erro.
"""
from __future__ import annotations

import pytest

from app.obs import galileo_obs

galileo = pytest.importorskip("galileo")

from galileo import galileo_context  # noqa: E402


# =============================================================================
# Dublês
# =============================================================================

class FakeTrace:
    def __init__(self, name: str, metadata: dict | None = None) -> None:
        self.name = name
        self.input = ""
        self.output = None
        self.user_metadata = metadata or {}
        self.spans: list = []
        self._parent = None


class FakeLogger:
    """`GalileoLogger` reduzido ao contrato que a D.3 usa (galileo==2.6.0)."""

    def __init__(self, *, start_raises=None, start_returns_none=False, conclude_raises=None,
                 flush_raises=None, parent=None) -> None:
        self.traces: list[FakeTrace] = []
        self.calls: list[str] = []
        self._parent = parent
        self._start_raises = start_raises
        self._start_returns_none = start_returns_none
        self._conclude_raises = conclude_raises
        self._flush_raises = flush_raises
        self.agent_control_enabled = 0
        self.concluded_all = None

    def current_parent(self):
        return self._parent

    def start_trace(self, input="", name=None, metadata=None, **_kwargs):
        self.calls.append("start_trace")
        if self._start_raises is not None:
            raise self._start_raises
        if self._start_returns_none:
            return None
        trace = FakeTrace(name, metadata)
        self.traces.append(trace)
        self._parent = trace
        return trace

    def conclude(self, output=None, conclude_all=False, **_kwargs):
        self.calls.append("conclude")
        self.concluded_all = conclude_all
        if self._conclude_raises is not None:
            raise self._conclude_raises
        self._parent = None

    def flush(self, **_kwargs):
        self.calls.append("flush")
        if self._flush_raises is not None:
            raise self._flush_raises
        return list(self.traces)

    def enable_agent_control(self):
        self.agent_control_enabled += 1


class FakeCallback:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


@pytest.fixture
def obs_live(monkeypatch):
    """Splunk Agent Observability "ligado" sem rede: contexto e sessão viram no-op, o logger é
    um dublê e o callback é registrado sem tocar no SDK."""
    from app.obs import galileo_callback as callback_module

    logger = FakeLogger()
    monkeypatch.setattr(galileo_obs, "is_enabled", lambda: True)
    monkeypatch.setattr(galileo_obs, "_logger_instance", lambda: logger)
    monkeypatch.setattr(callback_module, "VegaGalileoCallback", FakeCallback)
    # `session_scope` chama estes três explicitamente como atributo do singleton — dá pra
    # substituir na instância sem mexer na classe.
    monkeypatch.setattr(galileo_context, "__enter__", lambda *a, **k: galileo_context)
    monkeypatch.setattr(galileo_context, "__exit__", lambda *a, **k: None)
    monkeypatch.setattr(galileo_context, "start_session", lambda **k: None)
    return logger


# =============================================================================
# Ciclo de vida feliz
# =============================================================================

def test_live_trace_opens_named_by_feature_and_closes_on_exit(obs_live):
    assert galileo_obs.live_trace_active() is False

    with galileo_obs.session_scope("not-a-uuid", feature="chat") as session_id:
        assert galileo_obs.live_trace_active() is True
        assert obs_live.calls == ["start_trace"]
        trace = obs_live.traces[0]
        assert trace.name == "chat"
        assert trace.user_metadata["feature"] == "chat"
        assert trace.user_metadata["session_id"] == session_id
        trace.spans.append(object())  # o grafo rodou

    # `conclude` sem output: o SDK herda o do último filho (o workflow já compactado).
    assert obs_live.calls == ["start_trace", "conclude", "flush"]
    assert obs_live.concluded_all is True
    assert obs_live.traces == [trace]
    assert galileo_obs.live_trace_active() is False


def test_trace_without_feature_falls_back_to_a_generic_name(obs_live):
    with galileo_obs.session_scope():
        obs_live.traces[0].spans.append(object())
    assert obs_live.traces[0].name == "vega.request"


def test_shopper_session_name_from_active_uc():
    assert galileo_obs.shopper_session_name("uc-1") == "Session-UC1"
    assert galileo_obs.shopper_session_name("uc-5") == "Session-UC5"
    assert galileo_obs.shopper_session_name("") is None


def test_session_scope_passes_uc_name_to_start_session(obs_live, monkeypatch):
    from app.problems import FLAGS

    captured: dict = {}

    def _capture_start_session(**kwargs):
        captured.update(kwargs)
        return "sess-id"

    monkeypatch.setattr(galileo_context, "start_session", _capture_start_session)
    FLAGS.active_scenario = "uc-2"
    try:
        with galileo_obs.session_scope("550e8400-e29b-41d4-a716-446655440000", feature="chat"):
            pass
        assert captured["name"] == "Session-UC2"
        assert captured["external_id"] == "550e8400-e29b-41d4-a716-446655440000"
    finally:
        FLAGS.active_scenario = ""


def test_agent_control_bridge_is_registered_when_control_is_active(obs_live, monkeypatch):
    from app.obs import galileo_control

    monkeypatch.setattr(galileo_control, "is_active", lambda: True)
    with galileo_obs.session_scope(feature="chat"):
        pass
    # Sem o bridge registrado no logger, o sink "registered" do Agent Control fica vazio e
    # nenhum evento vira span `[control]` — por mais vivo que o trace esteja.
    assert obs_live.agent_control_enabled == 1


def test_agent_control_bridge_is_skipped_when_control_is_inactive(obs_live, monkeypatch):
    from app.obs import galileo_control

    monkeypatch.setattr(galileo_control, "is_active", lambda: False)
    with galileo_obs.session_scope(feature="chat"):
        pass
    assert obs_live.agent_control_enabled == 0


def test_empty_trace_is_dropped_instead_of_polluting_the_console(obs_live):
    # Request que não gerou span nenhum (cache/curto-circuito): no modo batch o trace nem
    # existiria ("No nodes to commit").
    with galileo_obs.session_scope(feature="chat"):
        pass
    assert obs_live.traces == []


# =============================================================================
# Callback: pendura no trace vivo, não abre um concorrente
# =============================================================================

def test_callback_hangs_on_the_live_trace(obs_live):
    with galileo_obs.session_scope(feature="chat"):
        (cb,) = galileo_obs.callbacks()
    assert cb.kwargs == {"start_new_trace": False, "flush_on_chain_end": False}


def test_callback_outside_a_live_trace_keeps_batch_mode(obs_live):
    (cb,) = galileo_obs.callbacks()
    assert cb.kwargs == {}


# =============================================================================
# Degradação — nada aqui pode derrubar o request
# =============================================================================

@pytest.mark.parametrize("boom", [
    RuntimeError("console fora do ar"),
    ValueError("trace já aberto"),
])
def test_start_trace_exception_degrades_to_batch_mode(monkeypatch, boom):
    from app.obs import galileo_callback as callback_module

    logger = FakeLogger(start_raises=boom)
    monkeypatch.setattr(galileo_obs, "is_enabled", lambda: True)
    monkeypatch.setattr(galileo_obs, "_logger_instance", lambda: logger)
    monkeypatch.setattr(callback_module, "VegaGalileoCallback", FakeCallback)
    monkeypatch.setattr(galileo_context, "__enter__", lambda *a, **k: galileo_context)
    monkeypatch.setattr(galileo_context, "__exit__", lambda *a, **k: None)
    monkeypatch.setattr(galileo_context, "start_session", lambda **k: None)

    with galileo_obs.session_scope(feature="chat") as session_id:
        # O request continua: sessão resolvida, callback no modo batch de antes da D.3.
        assert session_id is not None
        assert galileo_obs.live_trace_active() is False
        (cb,) = galileo_obs.callbacks()
        assert cb.kwargs == {}

    assert "conclude" not in logger.calls  # não há trace vivo pra concluir


def test_start_trace_returning_none_degrades_to_batch_mode(monkeypatch):
    # O SDK engole exceção de infra dentro do `start_trace` e devolve `None` em vez de levantar.
    logger = FakeLogger(start_returns_none=True)
    monkeypatch.setattr(galileo_obs, "is_enabled", lambda: True)
    monkeypatch.setattr(galileo_obs, "_logger_instance", lambda: logger)
    monkeypatch.setattr(galileo_context, "__enter__", lambda *a, **k: galileo_context)
    monkeypatch.setattr(galileo_context, "__exit__", lambda *a, **k: None)
    monkeypatch.setattr(galileo_context, "start_session", lambda **k: None)

    with galileo_obs.session_scope(feature="chat"):
        assert galileo_obs.live_trace_active() is False
    assert logger.calls == ["start_trace"]


def test_no_second_trace_when_one_is_already_open(monkeypatch):
    # Aninhamento inesperado: abrir outro trace faria o SDK levantar ValueError.
    already_open = FakeTrace("outro")
    logger = FakeLogger(parent=already_open)
    monkeypatch.setattr(galileo_obs, "is_enabled", lambda: True)
    monkeypatch.setattr(galileo_obs, "_logger_instance", lambda: logger)
    monkeypatch.setattr(galileo_context, "__enter__", lambda *a, **k: galileo_context)
    monkeypatch.setattr(galileo_context, "__exit__", lambda *a, **k: None)
    monkeypatch.setattr(galileo_context, "start_session", lambda **k: None)

    with galileo_obs.session_scope(feature="chat"):
        assert galileo_obs.live_trace_active() is False
    assert logger.calls == []


def test_conclude_and_flush_failures_never_reach_the_request(monkeypatch):
    logger = FakeLogger(
        conclude_raises=RuntimeError("rede caiu no conclude"),
        flush_raises=RuntimeError("rede caiu no flush"),
    )
    monkeypatch.setattr(galileo_obs, "is_enabled", lambda: True)
    monkeypatch.setattr(galileo_obs, "_logger_instance", lambda: logger)
    monkeypatch.setattr(galileo_context, "__enter__", lambda *a, **k: galileo_context)
    monkeypatch.setattr(galileo_context, "__exit__", lambda *a, **k: None)
    monkeypatch.setattr(galileo_context, "start_session", lambda **k: None)

    with galileo_obs.session_scope(feature="chat"):
        pass  # o `finally` roda os dois e engole os dois

    assert logger.calls == ["start_trace", "conclude", "flush"]
    assert galileo_obs.live_trace_active() is False


def test_body_exception_still_closes_the_trace(obs_live):
    with pytest.raises(RuntimeError):
        with galileo_obs.session_scope(feature="chat"):
            raise RuntimeError("erro de negócio no meio do request")
    assert obs_live.calls == ["start_trace", "conclude", "flush"]
    assert galileo_obs.live_trace_active() is False


def test_scope_is_a_noop_when_observability_is_off(monkeypatch):
    monkeypatch.setattr(galileo_obs, "is_enabled", lambda: False)
    with galileo_obs.session_scope(feature="chat") as session_id:
        assert session_id is None
        assert galileo_obs.live_trace_active() is False
        assert galileo_obs.callbacks() == []


# =============================================================================
# Raiz do trace: input compacto emprestado do workflow LangGraph
# =============================================================================

from app.obs.galileo_callback import VegaGalileoCallback  # noqa: E402


class SeedHandler:
    """Só o que `_seed_live_trace_input` consulta no handler do SDK."""

    def __init__(self, logger, *, start_new_trace=False) -> None:
        self._galileo_logger = logger
        self._start_new_trace = start_new_trace

    def get_node(self, run_id):
        return None

    def get_nodes(self):
        return {}

    async def async_start_node(self, *args, **kwargs):
        return None

    async def async_end_node(self, *args, **kwargs):
        return None


def _seeding_callback(*, start_new_trace=False, parent=None):
    cb = VegaGalileoCallback.__new__(VegaGalileoCallback)
    logger = FakeLogger(parent=parent)
    cb._handler = SeedHandler(logger, start_new_trace=start_new_trace)
    cb._dropped = {}
    return cb, logger


CHAT_STATE = {
    "intent": "store_policy",
    "answer": "A" * 900,
    "language": "en",
    "artifacts": {"policy": {}},
    "quality": {"grounded": True},
    "trace": [1, 2, 3],
    "messages": [{"role": "user", "content": "B" * 4000}],
}


async def test_root_workflow_lends_its_compact_input_to_the_live_trace():
    trace = FakeTrace("chat")
    cb, _logger = _seeding_callback(parent=trace)

    await cb.on_chain_start({"name": "chat.workflow"}, CHAT_STATE, run_id=_uuid(), parent_run_id=None)

    # Compactado pelas mesmas regras do output: preview da resposta, sem o histórico inteiro.
    # `request` (F-WORKSHOP-STAB-4) entra truncado a 500 — o teto sobe pra caber os dois previews.
    assert "answer_preview" in trace.input
    assert "messages" not in trace.input
    assert len(trace.input) < 1350


async def test_live_trace_input_is_set_once(monkeypatch):
    trace = FakeTrace("chat")
    cb, _logger = _seeding_callback(parent=trace)

    await cb.on_chain_start({"name": "chat.workflow"}, CHAT_STATE, run_id=_uuid(), parent_run_id=None)
    first = trace.input
    await cb.on_chain_start({"name": "feature.cart_crosssell"}, {"x": 1}, run_id=_uuid(), parent_run_id=None)
    assert trace.input == first


async def test_batch_mode_never_touches_a_trace():
    trace = FakeTrace("chat")
    cb, _logger = _seeding_callback(start_new_trace=True, parent=trace)
    await cb.on_chain_start({"name": "chat.workflow"}, CHAT_STATE, run_id=_uuid(), parent_run_id=None)
    assert trace.input == ""


async def test_child_chains_never_touch_the_trace_root():
    trace = FakeTrace("chat")
    cb, _logger = _seeding_callback(parent=trace)
    await cb.on_chain_start(
        {"name": "chat.answer_store_policy"}, CHAT_STATE, run_id=_uuid(), parent_run_id=_uuid(),
    )
    assert trace.input == ""


async def test_seeding_failure_does_not_break_the_chain_start(monkeypatch):
    from app.obs import galileo_callback as callback_module

    def boom(*_a, **_k):
        raise RuntimeError("serialização quebrada")

    monkeypatch.setattr(callback_module, "compact_trace_payload", boom)
    trace = FakeTrace("chat")
    cb, _logger = _seeding_callback(parent=trace)
    # Sem o guard, um erro aqui derrubaria o request inteiro no primeiro nó do grafo.
    await cb.on_chain_start({"name": "chat.workflow"}, CHAT_STATE, run_id=_uuid(), parent_run_id=None)
    assert trace.input == ""


def test_compaction_still_anchors_on_the_langchain_root():
    # A raiz do trace mudou de identidade (agora é o trace vivo), mas a compactação continua
    # ancorada no run raiz do LangChain — que nunca tem `parent_run_id`.
    from app.obs.galileo_trace_compact import should_compact_workflow_io

    assert should_compact_workflow_io("chat.workflow", None) is True
    assert should_compact_workflow_io("chat.workflow", _uuid()) is False


def _uuid():
    import uuid

    return uuid.uuid4()
