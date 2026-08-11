"""Suite fixtures (F-BACKEND-1). Everything offline: with no provider configured the cascade
falls back to the structurally-deterministic stub, so no test without a marker touches the network."""
from __future__ import annotations

import os
import tempfile

# Must be set BEFORE importing `app.*` — `app.settings` resolves config at import time, and the
# modules also read its values at import time (same ordering as the old `run_*.py` scripts).
os.environ.setdefault("DEPLOYMENT_ENVIRONMENT", "user-42")
# No env file: the suite runs only on OS environment + defaults, so it doesn't inherit the
# dev machine's `.env` credentials (which would put it online).
os.environ.setdefault("VEGA_ENV_FILE", "")
# Test DB isolated from the dev's real `vega.db` (DT-036) — same module-level reasoning as the
# two vars above: `app/store/db.py` freezes `DB_PATH = settings.orders_db` at import time, too
# late for a fixture. `setdefault` still leaves room for an explicit `ORDERS_DB` from the environment.
# Desired side effect: `llm/llm_config.py` derives `.vega-persist` from `dirname(DB_PATH)`,
# so the dev's persistence directory also stops being touched by the suite.
os.environ.setdefault("ORDERS_DB", os.path.join(tempfile.mkdtemp(), "vega.db"))

import pytest

from app.problems import FLAGS, ProblemFlags


@pytest.fixture(autouse=True)
def reset_problem_flags():
    """FLAGS is a global singleton (1 user per VM). Since tests toggle it, restore the
    original state after each one — otherwise a test leaks an injected problem into the next."""
    saved = FLAGS.to_dict()
    yield FLAGS
    for name, value in saved.items():
        setattr(FLAGS, name, value)


@pytest.fixture(autouse=True)
def _isolate_api_rate_limit(monkeypatch):
    """Offline suite doesn't inherit HTTP/LLM buckets between tests; `test_api_rate_limit` re-enables HTTP explicitly."""
    from app import rate_limit
    from app.llm import llm_cache
    from app.settings import settings

    monkeypatch.setattr(settings, "api_rate_enabled", False)
    rate_limit.reset_http_limiters()
    llm_cache.reset_state()
    yield
    rate_limit.reset_http_limiters()
    llm_cache.reset_state()


@pytest.fixture
def clean_cache():
    """Resets the LLM cache/limiter (F-022) before and after — test order doesn't change hit/miss."""
    from app.llm import llm_cache

    llm_cache.reset_state()
    yield llm_cache
    llm_cache.reset_state()


@pytest.fixture
def api_client():
    """TestClient for the real app. The import-time bootstraps already ran in `import app.api`."""
    from fastapi.testclient import TestClient

    from app.api import app

    with TestClient(app) as client:
        yield client


def default_flags() -> dict:
    """Default values of ProblemFlags — useful for asserting the `/api/problems` contract."""
    return ProblemFlags().to_dict()
