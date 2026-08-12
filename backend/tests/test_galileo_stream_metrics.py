"""`ensure_stream_metrics` (`obs/galileo_obs.py`) — F-GALILEO-EVAL-1.

Each workshop VM boots with its own `GALILEO_LOG_STREAM`; the stream must carry the module-6
evaluators, without ever clobbering a config the owner set in Console, and no Galileo failure may
block the boot. Two of these tests are regressions for defects seen against the real Console:

- a single unknown name (`correctness_aws_bedrock`) resolved inside the `enable_metrics(...)`
  argument raised `KeyError` AFTER the stream had been created — stream born, zero evaluators;
- and because the stream then existed, every later boot returned early, so it stayed empty forever.

The real SDK functions are monkeypatched at their import origin (`galileo.log_streams` /
`galileo.projects`) — the lazy imports inside the function resolve to the patched attributes.
"""
from __future__ import annotations

import pytest

from app.obs import galileo_obs
from app.settings import settings


class _FakeStream:
    def __init__(self, stream_id="stream-1", project_id="proj-1"):
        self.id = stream_id
        self.project_id = project_id
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
    # Custom scorers live only in the org's Console; pretend the configured ones all exist.
    monkeypatch.setattr(
        galileo_obs, "_known_custom_scorer_labels",
        lambda: set(galileo_obs.WORKSHOP_CUSTOM_METRICS),
    )


def _expected_metrics():
    from galileo.schema.metrics import GalileoMetrics

    return [GalileoMetrics[n] for n in galileo_obs.WORKSHOP_METRICS] + list(
        galileo_obs.WORKSHOP_CUSTOM_METRICS
    )


def _patch_sdk(monkeypatch, *, project, stream, created_stream=None, scorer_count=0):
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
    monkeypatch.setattr(galileo_obs, "_configured_scorer_count", lambda *_a: scorer_count)
    return fakes


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "galileo_api_key", "")
    fakes = _patch_sdk(monkeypatch, project=None, stream=None)
    galileo_obs.ensure_stream_metrics()
    assert fakes["get_project"].calls == []


def test_new_stream_gets_workshop_metrics(monkeypatch, enabled_key):
    created = _FakeStream()
    fakes = _patch_sdk(monkeypatch, project=object(), stream=None, created_stream=created)
    galileo_obs.ensure_stream_metrics()
    assert len(fakes["create_log_stream"].calls) == 1
    assert created.enabled_with == _expected_metrics()  # presets (enum) + custom (labels)
    assert fakes["create_project"].calls == []  # project existed


def test_missing_custom_scorer_does_not_sink_the_presets(monkeypatch, enabled_key):
    """A custom scorer only exists in the org that built it — absence must cost only itself."""
    monkeypatch.setattr(galileo_obs, "WORKSHOP_CUSTOM_METRICS", ("No Such Custom Scorer",))
    monkeypatch.setattr(galileo_obs, "_known_custom_scorer_labels", lambda: {"Something Else"})
    created = _FakeStream()
    _patch_sdk(monkeypatch, project=object(), stream=None, created_stream=created)
    galileo_obs.ensure_stream_metrics()
    assert len(created.enabled_with) == len(galileo_obs.WORKSHOP_METRICS)
    assert "No Such Custom Scorer" not in created.enabled_with


def test_unreadable_catalog_still_sends_custom_labels(monkeypatch, enabled_key):
    """Catalog unreadable ≠ scorer absent: let the write attempt be the judge."""
    monkeypatch.setattr(galileo_obs, "WORKSHOP_CUSTOM_METRICS", ("Correctness AWS Bedrock",))
    monkeypatch.setattr(galileo_obs, "_known_custom_scorer_labels", lambda: None)
    created = _FakeStream()
    _patch_sdk(monkeypatch, project=object(), stream=None, created_stream=created)
    galileo_obs.ensure_stream_metrics()
    assert "Correctness AWS Bedrock" in created.enabled_with


def test_configured_stream_untouched(monkeypatch, enabled_key):
    """The owner's Console picks survive restarts."""
    existing = _FakeStream()
    fakes = _patch_sdk(monkeypatch, project=object(), stream=existing, scorer_count=3)
    galileo_obs.ensure_stream_metrics()
    assert fakes["create_log_stream"].calls == []
    assert existing.enabled_with is None


def test_empty_existing_stream_is_backfilled(monkeypatch, enabled_key):
    """Regression: a stream created without evaluators must not stay empty forever."""
    existing = _FakeStream()
    fakes = _patch_sdk(monkeypatch, project=object(), stream=existing, scorer_count=0)
    galileo_obs.ensure_stream_metrics()
    assert fakes["create_log_stream"].calls == []
    assert existing.enabled_with == _expected_metrics()


def test_unknown_scorer_count_leaves_stream_alone(monkeypatch, enabled_key):
    """Can't prove the config is empty → don't risk clobbering it."""
    existing = _FakeStream()
    _patch_sdk(monkeypatch, project=object(), stream=existing, scorer_count=None)
    galileo_obs.ensure_stream_metrics()
    assert existing.enabled_with is None


def test_missing_project_is_created(monkeypatch, enabled_key):
    created = _FakeStream()
    fakes = _patch_sdk(monkeypatch, project=None, stream=None, created_stream=created)
    galileo_obs.ensure_stream_metrics()
    assert len(fakes["create_project"].calls) == 1
    assert created.enabled_with is not None


def test_unknown_name_does_not_sink_the_valid_ones(monkeypatch, enabled_key):
    """Regression for `correctness_aws_bedrock`: one bad name cost ALL evaluators."""
    monkeypatch.setattr(galileo_obs, "WORKSHOP_METRICS", ("correctness", "nope_not_a_metric"))
    monkeypatch.setattr(galileo_obs, "WORKSHOP_CUSTOM_METRICS", ())
    created = _FakeStream()
    _patch_sdk(monkeypatch, project=object(), stream=None, created_stream=created)
    galileo_obs.ensure_stream_metrics()
    assert [m.name for m in created.enabled_with] == ["correctness"]


def test_all_names_unknown_writes_nothing(monkeypatch, enabled_key):
    monkeypatch.setattr(galileo_obs, "WORKSHOP_METRICS", ("nope_not_a_metric",))
    monkeypatch.setattr(galileo_obs, "WORKSHOP_CUSTOM_METRICS", ())
    fakes = _patch_sdk(monkeypatch, project=object(), stream=None, created_stream=_FakeStream())
    galileo_obs.ensure_stream_metrics()
    assert fakes["create_log_stream"].calls == []  # nothing created without a valid metric


def test_every_configured_name_exists_in_the_sdk_enum():
    """Guards the constant itself: a typo here is invisible until a VM boots."""
    from galileo.schema.metrics import GalileoMetrics

    unknown = [n for n in galileo_obs.WORKSHOP_METRICS if n not in GalileoMetrics.__members__]
    assert unknown == [], f"unknown GalileoMetrics member(s): {unknown}"


class _FakeConfig:
    api_client = object()


class _MetricSettings:
    def __init__(self, scorers):
        self.scorers = scorers


class _ValidationError:
    """`HTTPValidationError` shape: the endpoint RETURNS it instead of raising, and it has
    no `scorers` — reading that as 0 would let the backfill overwrite a real config."""


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_MetricSettings([]), 0),            # genuinely empty stream
        (_MetricSettings(["a", "b"]), 2),    # configured stream
        (_ValidationError(), None),          # error response — must NOT read as empty
        (None, None),                        # no response at all
    ],
)
def test_configured_scorer_count_parses_real_response_shapes(monkeypatch, response, expected):
    # The config singleton needs a credential before the endpoint is ever reached.
    monkeypatch.setattr(
        "galileo.config.GalileoPythonConfig.get", classmethod(lambda _cls: _FakeConfig()),
    )
    monkeypatch.setattr(
        "galileo.resources.api.log_stream"
        ".get_metric_settings_projects_project_id_log_streams_log_stream_id_metric_settings_get.sync",
        lambda **_kwargs: response,
    )
    assert galileo_obs._configured_scorer_count("proj-1", "stream-1") == expected


def test_sdk_failure_never_escapes(monkeypatch, enabled_key):
    def _boom(**_kwargs):
        raise RuntimeError("console unreachable")

    monkeypatch.setattr("galileo.projects.get_project", _boom)
    galileo_obs.ensure_stream_metrics()  # must not raise
