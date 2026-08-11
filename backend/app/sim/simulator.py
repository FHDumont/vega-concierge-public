"""Vega traffic simulator — concurrent session engine (F-018).

`SimulatorEngine`/`ENGINE`: realistic and continuous traffic. Creates **pool of N test users**
(EN names, distributed tier) and maintains **N concurrent journeys** (asyncio).
Each journey logs in, browses (catalog/category/search/detail), **optionally uses
Concierge**, and **always completes checkout**; when done, slot **waits** and
**randomly picks** another user from pool — maintaining N active journeys. Drives in-process
SERVICE LAYER (same functions endpoints wrap), with blocking parts in `asyncio.to_thread`
to not block uvicorn event loop. Traverses SAME path as real traffic's
`run_workflow`/`place_order` (ADR-014).

(F-017's one-off order robot — `simulate()`/CLI/`POST /api/admin/simulate` — was
removed in F-018: concurrent session simulator completely replaces it.)
"""
import asyncio
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..store import users
from ..problems import FLAGS
from ..store.tools import CATALOG


def random_cart(max_lines: int = 3, max_qty: int = 2) -> list[dict]:
    """Synthetic cart: 1..max_lines distinct catalog SKUs, qty 1..max_qty.
    Item snapshots (sku/name/price), as real checkout sends. Fallback from
    `_weighted_cart` when no category has items in stock."""
    lines = random.randint(1, max_lines)
    picks = random.sample(CATALOG, min(lines, len(CATALOG)))
    return [
        {"sku": p["sku"], "name": p["name"], "qty": random.randint(1, max_qty), "price": p["price"]}
        for p in picks
    ]


# ---------------------------------------------------------------------------
# Concurrent session engine (F-018)
# ---------------------------------------------------------------------------

# Test user pool: names IN ENGLISH (CONVENTIONS: demo UI/data in English).
_EN_FIRST = ["Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia", "Mason", "Isabella",
             "Lucas", "Mia", "Logan", "Amelia", "James", "Harper", "Henry", "Ella", "Jack",
             "Grace", "Owen"]
_EN_LAST = ["Carter", "Bennett", "Hayes", "Morgan", "Reed", "Brooks", "Foster", "Sullivan",
            "Parker", "Coleman", "Hughes", "Russell", "Bryant", "Powell", "Ross", "Murphy"]

_SIM_PASSWORD = "sim1234"  # fixed password from demo pool (same nature as DEMO; DT-010)
_SIM_STREETS = ["Market St", "Oak Ave", "Maple Rd", "Pine Ln", "Cedar Blvd", "Elm Way", "Bay St"]
_SIM_CITIES = ["Springfield", "Riverton", "Fairview", "Lakeside", "Hillcrest", "Brookfield"]

# Displayed categories → catalog tag (mirrors frontend/lib/shop.ts).
_CATEGORY_TAG = {"Audio": "audio", "Wearables": "wearable", "Home": "casa", "Gifts": "presente"}
_CATEGORIES = list(_CATEGORY_TAG.keys())
_TIERS = ["STANDARD", "GOLD", "PLATINUM"]

# Traffic modes (F-039): "api" drives in-process service layer (F-018);
# "browser" drives real browser (Playwright) via UI, for RUM to capture sessions.
_MODES = ["api", "browser"]
# Browser heavier than API → lower, safer concurrency ceiling in browser mode.
_BROWSER_MAX_CONCURRENCY = 12

# Problems eligible for injection per journey (subset of ProblemPanel toggles).
_INJECTABLE = ["price_hallucination", "payment_outage", "inventory_outage", "fraud_false_positive"]

# Example requests for Concierge (UI in English). Budget fits catalog.
_CONCIERGE_PROMPTS = [
    "a birthday gift under $300",
    "noise-cancelling headphones for travel",
    "a smartwatch for running",
    "something nice for my kitchen",
    "a present for a coffee lover",
    "a gaming headset under $250",
    "an air purifier for allergies",
]


@dataclass
class SimConfig:
    """Complete simulator config (all knobs come from UI). `concurrency` (N) is
    pool size AND number of concurrent journeys — single N (phase criterion)."""
    mode: str = "api"                # api | browser (F-039): in-process API vs real browser
    concurrency: int = 5
    wait_min_s: float = 1.0          # wait between journeys (idle slot)
    wait_max_s: float = 4.0
    think_min_s: float = 0.4         # think-time between navigation actions
    think_max_s: float = 1.5
    actions_min: int = 2             # number of nav actions before checkout
    actions_max: int = 6
    concierge_pct: int = 40          # % of journeys using AI Concierge
    problem_pct: int = 0             # % of journeys injecting problem at checkout
    problems: list[str] = field(default_factory=lambda: list(_INJECTABLE))
    category_mix: dict[str, int] = field(
        default_factory=lambda: {c: 1 for c in _CATEGORIES})  # weight per category in cart
    tier_mix: dict[str, int] = field(
        default_factory=lambda: {"STANDARD": 3, "GOLD": 2, "PLATINUM": 1})  # tier of created
    speed: float = 1.0               # multiplier for sleeps (<1 = quick demo; 1 = realistic)
    target_kind: str = "none"        # none | orders | duration
    target_value: int = 0            # number of orders OR seconds, per target_kind
    reset: bool = False              # clear orders before starting (integrates with Admin)
    max_lines: int = 3               # distinct SKUs per cart
    max_qty: int = 2                 # max qty per line

    @classmethod
    def from_dict(cls, d: dict | None) -> "SimConfig":
        """Builds the config from the screen payload, with defaults and clamps."""
        d = d or {}
        cfg = cls()
        cfg.mode = d.get("mode", cfg.mode) if d.get("mode") in _MODES else cfg.mode
        # Browser is heavy → lower concurrency ceiling in this mode (F-039).
        max_conc = _BROWSER_MAX_CONCURRENCY if cfg.mode == "browser" else 50
        cfg.concurrency = _clamp_int(d.get("concurrency", cfg.concurrency), 1, max_conc)
        cfg.wait_min_s = _clamp_float(d.get("wait_min_s", cfg.wait_min_s), 0.0, 600.0)
        cfg.wait_max_s = _clamp_float(d.get("wait_max_s", cfg.wait_max_s), cfg.wait_min_s, 600.0)
        cfg.think_min_s = _clamp_float(d.get("think_min_s", cfg.think_min_s), 0.0, 60.0)
        cfg.think_max_s = _clamp_float(d.get("think_max_s", cfg.think_max_s), cfg.think_min_s, 60.0)
        cfg.actions_min = _clamp_int(d.get("actions_min", cfg.actions_min), 0, 50)
        cfg.actions_max = _clamp_int(d.get("actions_max", cfg.actions_max), cfg.actions_min, 50)
        cfg.concierge_pct = _clamp_int(d.get("concierge_pct", cfg.concierge_pct), 0, 100)
        cfg.problem_pct = _clamp_int(d.get("problem_pct", cfg.problem_pct), 0, 100)
        probs = d.get("problems", cfg.problems)
        cfg.problems = [p for p in probs if p in _INJECTABLE] if isinstance(probs, list) else cfg.problems
        if isinstance(d.get("category_mix"), dict):
            cfg.category_mix = {c: _clamp_int(d["category_mix"].get(c, 0), 0, 100) for c in _CATEGORIES}
        if isinstance(d.get("tier_mix"), dict):
            cfg.tier_mix = {t: _clamp_int(d["tier_mix"].get(t, 0), 0, 100) for t in _TIERS}
        cfg.speed = _clamp_float(d.get("speed", cfg.speed), 0.05, 20.0)
        cfg.target_kind = d.get("target_kind", cfg.target_kind)
        if cfg.target_kind not in ("none", "orders", "duration"):
            cfg.target_kind = "none"
        cfg.target_value = _clamp_int(d.get("target_value", cfg.target_value), 0, 1_000_000)
        cfg.reset = bool(d.get("reset", cfg.reset))
        cfg.max_lines = _clamp_int(d.get("max_lines", cfg.max_lines), 1, len(CATALOG))
        cfg.max_qty = _clamp_int(d.get("max_qty", cfg.max_qty), 1, 5)
        return cfg

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "concurrency": self.concurrency,
            "wait_min_s": self.wait_min_s, "wait_max_s": self.wait_max_s,
            "think_min_s": self.think_min_s, "think_max_s": self.think_max_s,
            "actions_min": self.actions_min, "actions_max": self.actions_max,
            "concierge_pct": self.concierge_pct, "problem_pct": self.problem_pct,
            "problems": self.problems, "category_mix": self.category_mix, "tier_mix": self.tier_mix,
            "speed": self.speed, "target_kind": self.target_kind, "target_value": self.target_value,
            "reset": self.reset, "max_lines": self.max_lines, "max_qty": self.max_qty,
        }


def _clamp_int(v, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return lo


def _clamp_float(v, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return lo


def _distribute(n: int, weights: dict[str, int], keys: list[str]) -> list[str]:
    """Distributes n items among `keys` proportional to the weights (remainder to the highest weights)."""
    total = sum(max(0, weights.get(k, 0)) for k in keys) or 1
    counts = {k: n * max(0, weights.get(k, 0)) // total for k in keys}
    rest = n - sum(counts.values())
    for k in sorted(keys, key=lambda k: weights.get(k, 0), reverse=True):
        if rest <= 0:
            break
        counts[k] += 1
        rest -= 1
    out: list[str] = []
    for k in keys:
        out.extend([k] * counts[k])
    random.shuffle(out)
    return out or [keys[0]] * n


def _sim_address() -> str:
    return f"{random.randint(1, 999)} {random.choice(_SIM_STREETS)}, {random.choice(_SIM_CITIES)}"


def _ensure_pool(n: int, tier_mix: dict[str, int]) -> list[dict]:
    """Guarantees exactly N test users (deterministic emails → idempotent:
    reuses existing ones, creates the missing ones). EN names, fixed password, distributed tier
    (initial label written directly to the column; the app would recompute it from spend — ADR-008).
    Returns the pool's user list."""
    tiers = _distribute(n, tier_mix, _TIERS)
    pool: list[dict] = []
    for i in range(n):
        email = f"sim.shopper{i + 1:02d}@vega.sim"
        user = users.get_user_by_email(email)
        if user is None:
            name = f"{random.choice(_EN_FIRST)} {random.choice(_EN_LAST)}"
            user = users.register(name, email, _SIM_PASSWORD)
            users.update_address(user["id"], _sim_address())
        users.update_tier(user["id"], tiers[i])  # initial tier label (config distribution)
        pool.append({"id": user["id"], "name": user["name"], "email": email,
                     "address": user.get("address", ""), "tier": tiers[i]})
    return pool


def _weighted_cart(cfg: SimConfig) -> list[dict]:
    """Cart with category bias (persona mix). Picks 1..max_lines categories
    by weight, chooses one IN-STOCK SKU from each (distinct). Snapshots like the real
    checkout. If there's no stock, falls back to `random_cart` (the journey still closes checkout)."""
    weights = [max(0, cfg.category_mix.get(c, 0)) for c in _CATEGORIES]
    if sum(weights) == 0:
        weights = [1] * len(_CATEGORIES)
    lines = random.randint(1, cfg.max_lines)
    picked: dict[str, dict] = {}
    for _ in range(lines * 3):  # a few tries to find distinct in-stock SKUs
        if len(picked) >= lines:
            break
        cat = random.choices(_CATEGORIES, weights=weights, k=1)[0]
        tag = _CATEGORY_TAG[cat]
        opts = [p for p in CATALOG if tag in p["tags"] and p["stock"] > 0 and p["sku"] not in picked]
        if opts:
            p = random.choice(opts)
            picked[p["sku"]] = {"sku": p["sku"], "name": p["name"],
                                "qty": random.randint(1, cfg.max_qty), "price": p["price"]}
    return list(picked.values()) or random_cart(cfg.max_lines, cfg.max_qty)


class SimulatorEngine:
    """Asyncio engine for concurrent sessions. Singleton (`ENGINE`); 1 VM per
    participant, no multi-tenant. Keeps N workers (slots) that run journeys in a
    loop. Live state in memory (live panel); nothing besides the orders is persisted."""

    def __init__(self) -> None:
        self.cfg = SimConfig()
        self.status = "stopped"           # stopped | running | paused
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._inject_lock = asyncio.Lock()  # serializes problem-injection windows
        self._browser = None              # active BrowserRunner in browser mode (F-039)
        self.pool: list[dict] = []
        # Live state (live panel):
        self.slots: list[dict] = []       # [{slot, user, action, journeys}]
        self.started_at: float = 0.0      # time.monotonic() of the start
        self.started_iso: str | None = None
        self.completed = 0                # closed orders (any status)
        self.paid = 0                     # PAID orders
        self.injected = 0                 # journeys that injected a problem (% problem)
        self.by_status: dict[str, int] = {}
        self.errors = 0                   # unexpected exceptions in journeys

    # --- lifecycle -----------------------------------------------------

    async def start(self, cfg: SimConfig) -> dict:
        """(Re)starts the engine with `cfg`. Stops the previous one if there is one, optionally
        clears orders (reset), guarantees the pool of N users, and spins up N workers + monitor."""
        await self.stop()
        self.cfg = cfg
        if cfg.reset:
            from ..store import orders
            await asyncio.to_thread(orders.clear_all)
        self.pool = await asyncio.to_thread(_ensure_pool, cfg.concurrency, cfg.tier_mix)
        if cfg.mode == "browser":
            from . import sim_browser
            runner = sim_browser.BrowserRunner()
            await runner.start()          # spins up Playwright + headless Chromium (1 per engine)
            self._browser = runner
        self.slots = [{"slot": i + 1, "user": None, "tier": None, "action": "idle",
                       "journeys": 0, "last": None}
                      for i in range(cfg.concurrency)]
        self.completed = self.paid = self.injected = self.errors = 0
        self.by_status = {}
        self._stop = asyncio.Event()
        self.started_at = time.monotonic()
        self.started_iso = datetime.now(timezone.utc).isoformat()
        self.status = "running"
        self._tasks = [asyncio.create_task(self._worker(self.slots[i])) for i in range(cfg.concurrency)]
        if cfg.target_kind != "none" and cfg.target_value > 0:
            self._tasks.append(asyncio.create_task(self._monitor()))
        return self.status_dict()

    async def stop(self) -> dict:
        """Stops the engine: signals, waits for the workers, and clears the slots."""
        if self.status == "stopped" and not self._tasks:
            return self.status_dict()
        self.status = "stopped"
        self._stop.set()
        tasks = [t for t in self._tasks if t is not asyncio.current_task()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks = []
        if self._browser is not None:    # closes Chromium only after the contexts (F-039)
            try:
                await self._browser.stop()
            finally:
                self._browser = None
        for s in self.slots:
            s["user"], s["tier"], s["action"] = None, None, "idle"
        return self.status_dict()

    def pause(self, paused: bool) -> dict:
        """Pause/resume. Paused keeps the workers alive, but idle."""
        if self.status in ("running", "paused"):
            self.status = "paused" if paused else "running"
        return self.status_dict()

    # --- worker / journey --------------------------------------------------

    async def _sleep(self, seconds: float) -> None:
        """Cancellable sleep: returns early if the engine is stopped."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            pass

    def _scaled(self, lo: float, hi: float) -> float:
        return random.uniform(lo, hi) * self.cfg.speed

    async def _worker(self, slot: dict) -> None:
        """Loop for a slot: picks a random user, runs the journey, waits, repeats —
        keeping one active journey in this slot while the engine runs."""
        while self.status != "stopped":
            if self.status == "paused":
                slot["action"] = "paused"
                await self._sleep(0.2)
                continue
            user = random.choice(self.pool)   # random pick from the pool
            slot["user"] = user["name"]
            slot["tier"] = user["tier"]
            try:
                await self._journey(slot, user)
                slot["journeys"] += 1
            except Exception:                  # a journey never brings down the worker
                self.errors += 1
            if self.status == "stopped":
                break
            slot["action"] = "waiting"
            await self._sleep(self._scaled(self.cfg.wait_min_s, self.cfg.wait_max_s))
        slot["action"] = "idle"

    async def _journey(self, slot: dict, user: dict) -> None:
        """One journey (API or Browser mode): login → navigate (N actions, optional concierge)
        → ALWAYS closes a checkout (purchase). Dispatches by mode and tallies the result
        (PAID/FAILED) at a single point. `status is None` = engine stopped mid-way."""
        inject = (random.randint(1, 100) <= self.cfg.problem_pct) and bool(self.cfg.problems)
        if self.cfg.mode == "browser":
            status = await self._journey_browser(slot, user, inject)
        else:
            status = await self._journey_api(slot, user, inject)
        if status is None:
            return
        slot["last"] = status
        self.completed += 1
        self.by_status[status] = self.by_status.get(status, 0) + 1
        if status == "PAID":
            self.paid += 1

    async def _journey_api(self, slot: dict, user: dict, inject: bool) -> str | None:
        """API mode (F-018): drives the in-process service layer (real login/concierge/checkout,
        blocking parts in `to_thread`). Returns the order status."""
        slot["action"] = "signing in"
        token = await asyncio.to_thread(_login, user["email"])
        try:
            n_actions = random.randint(self.cfg.actions_min, self.cfg.actions_max)
            for _ in range(n_actions):
                if self.status == "stopped":
                    return None
                while self.status == "paused":
                    await self._sleep(0.2)
                if random.randint(1, 100) <= self.cfg.concierge_pct:
                    slot["action"] = "asking the concierge"
                    try:
                        await asyncio.to_thread(_run_concierge)
                    except Exception:
                        pass  # injected problem (blast radius) in navigation doesn't abort the purchase
                else:
                    slot["action"] = _browse_label()
                await self._sleep(self._scaled(self.cfg.think_min_s, self.cfg.think_max_s))
            slot["action"] = "checking out"
            items = _weighted_cart(self.cfg)
            customer = {"name": user["name"], "email": user["email"], "address": user["address"]}
            async with self._inject_guard(inject):
                return await asyncio.to_thread(_place_order, items, customer, user["id"])
        finally:
            if token:
                users.drop_session(token)

    async def _journey_browser(self, slot: dict, user: dict, inject: bool) -> str | None:
        """Browser mode (F-039): drives headless Chromium through the UI (Playwright). Reuses the
        config/panel; think-time and the stop/pause cutoff go through the engine."""
        async def think() -> None:
            while self.status == "paused":
                await self._sleep(0.2)
            await self._sleep(self._scaled(self.cfg.think_min_s, self.cfg.think_max_s))

        return await self._browser.run_journey(
            user=user, cfg=self.cfg,
            set_action=lambda label: slot.__setitem__("action", label),
            think=think,
            is_stopped=lambda: self.status == "stopped",
            checkout_guard=lambda: self._inject_guard(inject),
        )

    @asynccontextmanager
    async def _inject_guard(self, inject: bool):
        """Turns on a problem toggle only during the checkout window (serialized to avoid corrupting
        the global FLAGS — restores the previous value). Blast radius across concurrent checkouts
        is intentional (ADR-014/DT-011). `inject=False` → no-op."""
        if not inject:
            yield
            return
        flag = random.choice(self.cfg.problems)
        self.injected += 1
        async with self._inject_lock:
            prev = getattr(FLAGS, flag)
            setattr(FLAGS, flag, True)
            try:
                yield
            finally:
                setattr(FLAGS, flag, prev)

    async def _monitor(self) -> None:
        """Stops the engine once it hits the target (number of orders or duration)."""
        while self.status != "stopped":
            if self.status != "paused":
                if self.cfg.target_kind == "orders" and self.completed >= self.cfg.target_value:
                    break
                if self.cfg.target_kind == "duration" and self.uptime_s() >= self.cfg.target_value:
                    break
            await self._sleep(0.5)
        if self.status != "stopped":
            self.status = "stopped"
            self._stop.set()

    # --- snapshot ----------------------------------------------------------

    def uptime_s(self) -> float:
        return time.monotonic() - self.started_at if self.started_iso else 0.0

    def status_dict(self) -> dict:
        up = self.uptime_s()
        opm = round(self.completed / up * 60, 1) if up > 0 else 0.0
        return {
            "status": self.status,
            "config": self.cfg.to_dict(),
            "pool_size": len(self.pool),
            "uptime_s": round(up, 1),
            "completed": self.completed,
            "paid": self.paid,
            "injected": self.injected,
            "orders_per_min": opm,
            "errors": self.errors,
            "by_status": self.by_status,
            "target": {"kind": self.cfg.target_kind, "value": self.cfg.target_value},
            "sessions": [dict(s) for s in self.slots],
        }


# --- in-process helpers (run in a thread; blocking) ----------------------

def _login(email: str) -> str | None:
    """Real login (checks password + creates session); returns the token (or None)."""
    user = users.authenticate(email, _SIM_PASSWORD)
    return users.create_session(user["id"]) if user else None


def _run_concierge() -> None:
    """Runs the Concierge workflow (same path as real traffic)."""
    from ..ai_agents.concierge_workflow import workflow
    from ..runnable_config import resolve_config, set_current_runnable_config

    config = resolve_config(None, feature="concierge")
    token = set_current_runnable_config(config)
    try:
        workflow.invoke(
            {"request": random.choice(_CONCIERGE_PROMPTS), "messages": [], "trace": []},
            config=config,
        )
    finally:
        set_current_runnable_config(None, token)


def _place_order(items: list[dict], customer: dict, user_id: str) -> str:
    """Closes the order through the single path and returns the status."""
    from ..store import checkout
    return checkout.place_order(items, customer, user_id)["status"]


def _browse_label() -> str:
    """Label for a navigation action for the live panel (UI in English)."""
    kind = random.choice(["catalog", "category", "search", "detail"])
    if kind == "catalog":
        return "browsing catalog"
    if kind == "category":
        return f"browsing {random.choice(_CATEGORIES)}"
    if kind == "search":
        return f"searching '{random.choice(['headphones', 'watch', 'coffee', 'lamp', 'gift'])}'"
    return f"viewing {random.choice(CATALOG)['name']}"


# Engine singleton (1 VM per participant).
ENGINE = SimulatorEngine()
