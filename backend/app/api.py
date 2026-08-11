"""Vega Concierge FastAPI. Store + Behind the Scenes.

This module ONLY MOUNTS the app: state bootstrap in `lifespan`, CORS and router registration.
Each route lives in its domain router, in `app/routers/` — no router uses `prefix`, so the
full path stays written on the route itself (the contract with the frontend is frozen:
`CONVENCOES.md` §DO NOT change).

Bootstrap runs on **startup**, not on import. Importing `app.api` (to inspect routes, for
example) no longer touches SQLite or initializes Agent Control; that's done by starting the app.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .hub import agent_config, feature_flags, hub, hub_settings, rum
from .llm import llm_config
from .obs import galileo_control
from .rate_limit import ApiRateLimitMiddleware
from .routers import ROUTERS
from .settings import settings
from .store import orders, users
from .store.tools import seed_workshop_stock

log = logging.getLogger(__name__)


def _bootstrap() -> None:
    """State the app needs up and running before the first request. All idempotent — runs
    the same on fresh boot, restart, and on each `--reload`."""
    log.info("config resolved (secrets appear as True/False only):\n%s",
             "\n".join(settings.summary_lines()))

    orders.init_db()  # create_all on boot (ADR-006)
    users.init_db()   # users table (F-008) + OWNER role (F-020)
    seed_workshop_stock()  # high inventory on boot; NS-005/NS-022 depleted for demo
    users.seed_demo_user()   # DEMO test user + history → tier GOLD (idempotent; F-010)
    users.seed_owner_user()  # OWNER user (owner-only LLM config; idempotent; F-020)
    llm_config.init_db()     # LLM provider table (F-020)
    _restored = llm_config.restore_providers_backup()  # fresh-state preserves LLM cascade (F-REAL-ENV-1)
    _seeded = llm_config.seed_providers_from_env()  # cascade `.env` + LLM_PROVIDER_PRIORITY (F-BACKEND-3)
    log.info(
        "llm providers: restored=%d, seed_env created=%d, keys updated=%d, order applied=%d",
        _restored, _seeded["created"], _seeded["updated"], _seeded["ordered"],
    )
    agent_config.init_db()      # config table per agent (F-021)
    agent_config.seed_defaults()  # seeds 6 agents with current prompts (idempotent; F-021)
    agent_config.migrate_f052_prompts()  # pre-F-052 prompts in SQLite → chatbot (F-052)
    hub_settings.init_db()      # local|remote source table (hub/peer — F-026)
    feature_flags.init_db()     # feature flags table for menu/surfaces (F-033)
    rum.init_db()               # Splunk RUM config table (snippet + toggle — F-040-RUM)
    hub.apply_source()          # installs active ConfigSource per settings (F-026)
    galileo_control.init_once()  # Agent Control / Protect (F-GALILEO-2, ADR-033)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _bootstrap()
    yield


app = FastAPI(title="Vega Concierge API", lifespan=lifespan)
app.add_middleware(ApiRateLimitMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for _router in ROUTERS:
    app.include_router(_router)
