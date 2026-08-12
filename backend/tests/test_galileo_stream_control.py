"""`ensure_stream_control` (`obs/galileo_control.py`) — F-GALILEO-CTRL-1.

Sibling of `test_galileo_stream_metrics.py`: each attendee's log stream is brand new, so the
module-8 prompt-injection control has to be created and attached (**disabled**) on boot, exactly
once, without ever touching what the attendee changed in the Console — and without any Galileo
failure keeping Agent Control from starting.

The SDK is monkeypatched at its import origin (`agent_control.controls` /
`agent_control.control_bindings` / `agent_control.client`); nothing here touches the network.
"""
from __future__ import annotations

import pytest

from app.obs import galileo_control, galileo_obs
from app.settings import settings

# The control of record, read from the real API (`GET /api/v1/controls/366`).
REFERENCE_DATA = {
    "condition": {
        "selector": {"path": "*"},
        "evaluator": {
            "name": "galileo.luna",
            "config": {
                "operator": "gte",
                "threshold": "0.7",
                "timeout_ms": 10000,
                "payload_field": "input",
                "scorer_id": "1e6a6237-7de8-4263-a5dc-bf0333577e7c",
                "scorer_label": "Prompt Injection (SLM)",
                "scorer_version_id": "ee490a00-c0bc-407d-9cd8-8dde5ff7ac30",
            },
        },
        "and": None, "or": None, "not": None,
    },
    "description": None,
    "enabled": True,
    "execution": "server",
    "scope": {
        "step_types": ["tool", "llm"], "step_names": None,
        "step_name_regex": None, "stages": ["pre"],
    },
    "action": {"decision": "deny", "steering_context": None},
    "tags": [], "template": None, "template_values": None,
}
SCORER_REF = (
    "1e6a6237-7de8-4263-a5dc-bf0333577e7c",
    "ee490a00-c0bc-407d-9cd8-8dde5ff7ac30",
    "Prompt Injection (SLM)",
)


class _Target:
    target_type = "log_stream"
    target_id = "aed9a43a-0000-0000-0000-000000000000"


class _FakeClient:
    """`AgentControlClient` stand-in — an async CM that records how it was built."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None


class _AsyncRecorder:
    def __init__(self, result=None, raises: Exception | None = None):
        self.result = result
        self.raises = raises
        self.calls: list = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.result


class _Recorder:
    """Sync callable stub that records calls and returns a fixed value."""

    def __init__(self, result=None):
        self.result = result
        self.calls: list = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class _HttpError(Exception):
    """`httpx.HTTPStatusError` shape: what matters here is `response.status_code`."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.response = type("_Resp", (), {"status_code": status_code})()


@pytest.fixture
def sdk(monkeypatch):
    """Patches the whole SDK surface and hands back the recorders."""
    monkeypatch.setattr(settings, "galileo_api_key", "fake-key")
    monkeypatch.setattr(galileo_obs, "preset_scorer_ref", lambda _name: SCORER_REF)
    fakes = {
        "list_controls": _AsyncRecorder({"controls": []}),
        "create_control": _AsyncRecorder({"control_id": 999}),
        "upsert": _AsyncRecorder({"ok": True}),
        "clients": [],
    }

    def _client(**kwargs):
        client = _FakeClient(**kwargs)
        fakes["clients"].append(client)
        return client

    monkeypatch.setattr("agent_control.client.AgentControlClient", _client)
    monkeypatch.setattr("agent_control.controls.list_controls", fakes["list_controls"])
    monkeypatch.setattr("agent_control.controls.create_control", fakes["create_control"])
    monkeypatch.setattr(
        "agent_control.control_bindings.upsert_control_binding_by_key", fakes["upsert"],
    )
    return fakes


def _expected_name() -> str:
    return f"{galileo_control.WORKSHOP_CONTROL_NAME}-{galileo_obs.log_stream()}"


def test_clean_stream_creates_the_control_and_binds_it_disabled(sdk):
    galileo_control.ensure_stream_control(_Target())

    (args, kwargs) = sdk["create_control"].calls[0]
    assert args[1] == _expected_name()
    assert args[2] == REFERENCE_DATA  # field by field against the control of record

    (_bind_args, bind_kwargs) = sdk["upsert"].calls[0]
    assert bind_kwargs["enabled"] is False  # the attendee is the one who turns it on
    assert bind_kwargs["target_type"] == "log_stream"
    assert bind_kwargs["target_id"] == _Target.target_id
    assert bind_kwargs["control_id"] == 999


def test_listing_is_scoped_to_the_target(sdk):
    """The API key is scoped: an unfiltered listing answers 401."""
    galileo_control.ensure_stream_control(_Target())

    (_args, kwargs) = sdk["list_controls"].calls[0]
    assert kwargs["attachment_target_type"] == "log_stream"
    assert kwargs["attachment_target_id"] == _Target.target_id
    assert kwargs["include_attachments"] is True


def test_already_attached_control_is_left_alone(sdk):
    """Whatever the attendee toggled or edited in the Console survives the restart."""
    sdk["list_controls"].result = {
        "controls": [{"id": 366, "name": _expected_name(), "attachments": [{"enabled": True}]}],
    }
    galileo_control.ensure_stream_control(_Target())
    assert sdk["create_control"].calls == []
    assert sdk["upsert"].calls == []


def test_homonymous_but_unattached_control_still_gets_bound(sdk):
    """The target filter is documented as filtering *attachments* — a name match with no
    attachment must not read as "already attached", or the stream never gets its binding."""
    sdk["list_controls"].result = {
        "controls": [{"id": 366, "name": _expected_name(), "attachments": []}],
    }
    sdk["create_control"].raises = _HttpError(409)  # the name is taken, as expected
    galileo_control.ensure_stream_control(_Target())
    assert len(sdk["create_control"].calls) == 1


def test_missing_control_id_does_not_bind(sdk, caplog):
    sdk["create_control"].result = {"configured": True}
    with caplog.at_level("ERROR"):
        galileo_control.ensure_stream_control(_Target())
    assert sdk["upsert"].calls == []
    assert "control_id" in caplog.text


def test_unresolved_scorer_creates_nothing(sdk, monkeypatch, caplog):
    monkeypatch.setattr(galileo_obs, "preset_scorer_ref", lambda _name: None)
    with caplog.at_level("ERROR"):
        galileo_control.ensure_stream_control(_Target())
    assert sdk["create_control"].calls == []
    assert sdk["upsert"].calls == []
    assert galileo_control.WORKSHOP_CONTROL_SCORER in caplog.text


def test_duplicate_name_does_not_bind_anything(sdk, caplog):
    """409 = the name is taken in the org; a second try under another name would only breed junk."""
    sdk["create_control"].raises = _HttpError(409)
    with caplog.at_level("WARNING"):
        galileo_control.ensure_stream_control(_Target())
    assert sdk["upsert"].calls == []
    assert _expected_name() in caplog.text


def test_api_failure_never_escapes(sdk):
    sdk["list_controls"].raises = RuntimeError("agent control unreachable")
    galileo_control.ensure_stream_control(_Target())  # must not raise


def test_control_failure_does_not_stop_agent_control_init(monkeypatch):
    """Regression guard for the call site: the seeding is best effort, `agent_control.init` isn't."""
    monkeypatch.setattr(settings, "galileo_api_key", "fake-key")
    monkeypatch.setattr(galileo_control, "_initialized", False)
    monkeypatch.setattr(galileo_control, "_decorated", True)  # no real `@control` decoration
    monkeypatch.setattr(
        galileo_control, "ensure_stream_control",
        lambda _target: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    init = _Recorder()
    monkeypatch.setattr("agent_control.init", init)
    monkeypatch.setattr("agent_control.settings.configure_settings", _Recorder())
    monkeypatch.setattr("galileo.galileo_context.init", _Recorder())
    monkeypatch.setattr("galileo.get_agent_control_target", lambda: _Target())

    galileo_control.init_once()
    assert len(init.calls) == 1
    assert galileo_control.is_active()


def test_configured_scorer_name_exists_in_the_sdk_catalog():
    """Guards the constant: a typo here is invisible until a VM boots (sibling of the metrics test)."""
    from galileo.schema.metrics import GalileoMetrics

    assert galileo_control.WORKSHOP_CONTROL_SCORER in GalileoMetrics.__members__


class _FakeScorer:
    def __init__(self, name, scorer_id, version_id, label=None):
        self.name = name
        self.id = scorer_id
        self.label = label
        self.default_version = type("_V", (), {"id": version_id})() if version_id else None


def _patch_catalog(monkeypatch, scorers):
    """Replaces the whole `Scorers` class — instantiating the real one needs a credential."""

    class _FakeScorers:
        def list(self, **_kwargs):
            if isinstance(scorers, Exception):
                raise scorers
            return scorers

    monkeypatch.setattr("galileo.scorers.Scorers", _FakeScorers)


def test_preset_scorer_ref_reads_id_and_default_version(monkeypatch):
    _patch_catalog(monkeypatch, [
        _FakeScorer("other", "x", "y"),
        _FakeScorer("prompt_injection_luna", SCORER_REF[0], SCORER_REF[1], SCORER_REF[2]),
    ])
    assert galileo_obs.preset_scorer_ref("prompt_injection_luna") == SCORER_REF


def test_preset_scorer_ref_is_none_when_absent_or_versionless(monkeypatch):
    _patch_catalog(monkeypatch, [_FakeScorer("other", "x", "y")])
    assert galileo_obs.preset_scorer_ref("prompt_injection_luna") is None
    _patch_catalog(monkeypatch, [_FakeScorer("prompt_injection_luna", "x", None)])
    assert galileo_obs.preset_scorer_ref("prompt_injection_luna") is None


def test_preset_scorer_ref_swallows_catalog_failure(monkeypatch):
    _patch_catalog(monkeypatch, RuntimeError("catalog unreachable"))
    assert galileo_obs.preset_scorer_ref("prompt_injection_luna") is None
