"""API REQUEST models (Pydantic) — extracted from `api.py` in F-BACKEND-1.

Input form only: zero logic, zero domain imports. Models are grouped by
the endpoint domain that consumes them, in the same order they appear in routers.

RESPONSE format doesn't live here — endpoints return the dicts that domain modules
already build, and that contract is frozen (`CONVENCOES.md` §DO NOT change).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RunRequest(BaseModel):
    request: str = "a birthday gift under $300"

class GiftRecommendRequest(BaseModel):
    request: str = "a birthday gift under $300"

class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str

class ChatContextIn(BaseModel):
    sku: str | None = None
    order_id: str | None = None

class ChatRequest(BaseModel):
    messages: list[ChatMessageIn]
    context: ChatContextIn | None = None

class SecurityActionRequest(BaseModel):
    action: Literal["delete_product", "export_recent_customers"]
    sku: str | None = None
    prompt: str | None = None

class ProductQARequest(BaseModel):
    sku: str
    question: str = ""
class CompareRequest(BaseModel):
    # Compare 2 products (F-029): coordinator → comparator + tools (real data).
    sku_a: str
    sku_b: str
class CartCrossSellRequest(BaseModel):
    # AI-Cart (F-023): cross-sell from current cart SKUs.
    skus: list[str] = []
class OrderItemIn(BaseModel):
    sku: str
    name: str
    qty: int
    price: float
class CustomerIn(BaseModel):
    name: str
    email: str
    address: str
class CreateOrderRequest(BaseModel):
    items: list[OrderItemIn]
    customer: CustomerIn
class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str
class LoginRequest(BaseModel):
    email: str
    password: str
class UpdateMeRequest(BaseModel):
    address: str
class ProblemUpdate(BaseModel):
    price_hallucination: bool | None = None
    fraud_false_positive: bool | None = None
    inventory_outage: bool | None = None
    latency_spike: bool | None = None
    cost_spike: bool | None = None
    payment_outage: bool | None = None
    payment_latency: bool | None = None
    refund_false_denial: bool | None = None  # F-029: denies an eligible refund (agent error)
    prompt_injection: bool | None = None  # UC-4: agent accepts buyer's price/policy override
    active_scenario: str | None = None  # active UC preset (uc-1..uc-5); "" clears
class InspectorToggle(BaseModel):
    # Toggles LLM Inspector on/off (F-023; owner-only).
    enabled: bool
class RumIn(BaseModel):
    # Partial edit of Splunk RUM config (F-040-RUM; owner-only). None = don't change.
    enabled: bool | None = None
    snippet: str | None = None
class FlagsIn(BaseModel):
    # Partial edit of menu feature flags (F-033; owner-only). None = don't change.
    behind_the_scenes: bool | None = None
    admin: bool | None = None
    simulator: bool | None = None
    inspector: bool | None = None
class SimStartRequest(BaseModel):
    # Advanced simulator config (F-018). All optional → defaults/clamps in SimConfig.from_dict.
    mode: str | None = None                  # api | browser (F-039): in-process API vs real browser
    concurrency: int | None = None          # N: pool size AND number of concurrent journeys
    wait_min_s: float | None = None         # wait between journeys (idle slot)
    wait_max_s: float | None = None
    think_min_s: float | None = None        # think-time between actions
    think_max_s: float | None = None
    actions_min: int | None = None          # number of navigation actions per journey
    actions_max: int | None = None
    concierge_pct: int | None = None        # % of journeys using Concierge
    problem_pct: int | None = None          # % of journeys that inject a problem
    problems: list[str] | None = None       # which problems eligible for injection
    category_mix: dict[str, int] | None = None  # weight per category in cart
    tier_mix: dict[str, int] | None = None      # distribution of tiers of created users
    speed: float | None = None              # multiplier for sleeps (<1 = fast demo)
    target_kind: str | None = None          # none | orders | duration
    target_value: int | None = None         # number of orders OR seconds
    reset: bool | None = None               # clear orders before starting
    max_lines: int | None = None
    max_qty: int | None = None
class SimPauseRequest(BaseModel):
    paused: bool = True
class ProviderIn(BaseModel):
    # Creates an LLM cascade provider (owner-only config — F-020). `api_key` is secret
    # (write-only; never returns to front). `kind`: openai | anthropic | bedrock.
    name: str
    kind: str = "openai"
    base_url: str = ""
    model: str
    api_key: str = ""
    enabled: bool = True
class ProviderUpdate(BaseModel):
    # Partial update. `api_key` empty/omitted KEEPS the current key (write-only).
    name: str | None = None
    kind: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    order: int | None = None
class ReorderIn(BaseModel):
    ids: list[str]
class TestProviderIn(BaseModel):
    # Live test of an unsaved provider (UI). If empty, tests the saved one by id.
    name: str | None = None
    kind: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
class AgentUpdate(BaseModel):
    # Config per agent (F-021). Partial fields; None keeps existing. No secret (goes raw to front).
    connection: str | None = None   # provider id (LP-xxxx) or '' = full cascade
    model: str | None = None        # optional model override
    role: str | None = None
    system_prompt: str | None = None
class AgentTestIn(BaseModel):
    # Live test of an agent: optional edits over saved → 1 real call to resolved LLM.
    connection: str | None = None
    model: str | None = None
    role: str | None = None
    system_prompt: str | None = None
class HubSourceIn(BaseModel):
    # Config source local|remote (hub/peer — F-026). Tokens are write-only (secret;
    # never return to front). `serve_token` accepts explicit '' (owner disables serving).
    source: str | None = None             # local | remote
    hub_url: str | None = None            # hub URL (client side)
    enrollment_token: str | None = None   # token to pull from hub (write-only)
    pull_interval_s: int | None = None
    serve_token: str | None = None        # token required to serve as hub
class EnrollIn(BaseModel):
    # Enroll RECEIVED (client side — F-027). Machine-to-machine: hub sends its own URL +
    # token for this store to pull config. Gated by ENROLL_TOKEN (lab secret), not owner.
    hub_url: str
    enrollment_token: str = ""
    pull_interval_s: int | None = None
class EnrollPushIn(BaseModel):
    # Enroll PUSH (hub side — F-027, owner-only): forces N stores (by IP) to become clients of this hub.
    ips: list[str]
    hub_url: str                          # URL of this hub (how targets reach it)
    enroll_token: str                     # shared secret to authenticate on targets (their ENROLL_TOKEN)
    enrollment_token: str                 # token that targets will use to pull (= this hub's serve_token)
    pull_interval_s: int | None = None
