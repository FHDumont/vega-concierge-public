"""Rate limit HTTP na borda — tiers exempt/ai/default (F-WORKSHOP-GUARD)."""
from __future__ import annotations

import pytest

from app import rate_limit
from app.settings import settings

_CHAT_BODY = {"messages": [{"role": "user", "content": "hello"}]}


@pytest.fixture(autouse=True)
def _reset_http_limiters():
    rate_limit.reset_http_limiters()
    yield
    rate_limit.reset_http_limiters()


@pytest.fixture
def rate_client(api_client, monkeypatch):
    """TestClient com limites baixos p/ burst previsível."""
    monkeypatch.setattr(settings, "api_rate_enabled", True)
    monkeypatch.setattr(settings, "api_rate_ai_max", 12)
    monkeypatch.setattr(settings, "api_rate_ai_window_s", 60)
    monkeypatch.setattr(settings, "api_rate_default_max", 60)
    monkeypatch.setattr(settings, "api_rate_default_window_s", 60)
    rate_limit.reset_http_limiters()
    return api_client


def test_burst_chat_returns_429_with_retry_after(rate_client):
    responses = [
        rate_client.post("/api/chat", json=_CHAT_BODY)
        for _ in range(15)
    ]
    limited = [r for r in responses if r.status_code == 429]
    assert len(limited) >= 3, [r.status_code for r in responses]
    sample = limited[0]
    body = sample.json()
    assert body["detail"] == "Too many requests. Please wait a moment and try again."
    assert isinstance(body["retry_after_seconds"], int)
    assert body["retry_after_seconds"] >= 1
    assert sample.headers.get("retry-after") == str(body["retry_after_seconds"])


def test_exempt_health_and_catalog_never_429(rate_client):
    for path in ("/api/health", "/api/catalog"):
        for _ in range(100):
            assert rate_client.get(path).status_code != 429, path


def test_disabled_api_rate_skips_429(rate_client, monkeypatch):
    monkeypatch.setattr(settings, "api_rate_enabled", False)
    rate_limit.reset_http_limiters()
    responses = [
        rate_client.post("/api/chat", json=_CHAT_BODY)
        for _ in range(20)
    ]
    assert all(r.status_code != 429 for r in responses)


def test_classify_route_ai_and_exempt():
    assert rate_limit.classify_route("POST", "/api/chat") == "ai"
    assert rate_limit.classify_route("GET", "/api/health") == "exempt"
    assert rate_limit.classify_route("POST", "/api/orders/abc/refund") == "ai"
    assert rate_limit.classify_route("GET", "/api/admin/summary") == "default"
