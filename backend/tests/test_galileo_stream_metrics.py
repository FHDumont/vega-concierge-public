"""`ensure_stream_metrics` (`obs/galileo_obs.py`) — F-GALILEO-EVAL-1.

Each workshop VM boots with its own `GALILEO_LOG_STREAM`; the stream must be born with the
module-6 evaluators enabled, but a stream that already exists is never touched (Console changes
survive restarts) and no Galileo failure may block the boot. The real SDK functions are
monkeypatched at their import origin (`galileo.log_streams` / `galileo.projects`) — the lazy
imports inside the function resolve to the patched attributes.
"""
from __future__ import annotations

import pytest

from app.obs import galileo_obs
from app.settings import settings


class _FakeStream:
    def __init__(self):
        self.enabled_with: list | None = None

    def enable_metrics(self, metrics):
        self.enabled_with = list(metrics)
        return []  # all server-side → no local metric configs


class _Recorder:
    """Callable stub that records calls and returns a fixed value."""

    def __init__(self, result=None):
        self.result = result
        self.calls: list = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


@pytest.fixture
def enabled_key(monkeypatch):
    monkeypatch.setattr(settings, "galileo_api_key", "fake-key")


def _patch_sdk(monkeypatch, *, project, stream, created_stream=None):
    fakes = {
        "get_project": _Recorder(project),
        "create_project": _Recorder(object()),
        "get_log_stream": _Recorder(stream),
        "create_log_stream": _Recorder(created_stream),
    }
    monkeypatch.setattr("galileo.projects.get_project", fakes["get_project"])
    monkeypatch.setattr("galileo.projects.create_project", fakes["create_project"])
    monkeypatch.setattr("galileo.log_streams.get_log_stream", fakes["get_log_stream"])
    monkeypatch.setattr("galileo.log_streams.create_log_stream", fakes["create_log_stream"])
    return fakes


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "galileo_api_key", "")
    fakes = _patch_sdk(monkeypatch, project=None, stream=None)
    galileo_obs.ensure_stream_metrics()
    assert fakes["get_project"].calls == []


def test_new_stream_gets_workshop_metrics(monkeypatch, enabled_key):
    from galileo.schema.metrics import GalileoMetrics

    created = _FakeStream()
    fakes = _patch_sdk(monkeypatch, project=object(), stream=None, created_stream=created)
    galileo_obs.ensure_stream_metrics()
    assert len(fakes["create_log_stream"].calls) == 1
    assert created.enabled_with == [GalileoMetrics[n] for n in galileo_obs.WORKSHOP_METRICS]
    assert fakes["create_project"].calls == []  # project existed


def test_existing_stream_untouched(monkeypatch, enabled_key):
    existing = _FakeStream()
    fakes = _patch_sdk(monkeypatch, project=object(), stream=existing)
    galileo_obs.ensure_stream_metrics()
    assert fakes["create_log_stream"].calls == []
    assert existing.enabled_with is None


def test_missing_project_is_created(monkeypatch, enabled_key):
    created = _FakeStream()
    fakes = _patch_sdk(monkeypatch, project=None, stream=None, created_stream=created)
    galileo_obs.ensure_stream_metrics()
    assert len(fakes["create_project"].calls) == 1
    assert created.enabled_with is not None


def test_sdk_failure_never_escapes(monkeypatch, enabled_key):
    def _boom(**_kwargs):
        raise RuntimeError("console unreachable")

    monkeypatch.setattr("galileo.projects.get_project", _boom)
    galileo_obs.ensure_stream_metrics()  # must not raise
