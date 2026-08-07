"""Simulador de tráfego do Vega — engine de sessões concorrentes (F-018).

`SimulatorEngine`/`ENGINE`: tráfego realista e contínuo. Cria um **pool de N usuários**
de teste (nomes EN, tier distribuído) e mantém **N jornadas simultâneas** (asyncio).
Cada jornada faz login, navega (catálogo/categoria/busca/detalhe), **opcionalmente usa
o Concierge** e **sempre fecha um checkout**; ao terminar, o slot **espera** e
**sorteia** outro usuário do pool — mantendo N jornadas ativas. Dirige a CAMADA DE
SERVIÇO in-process (as mesmas funções que os endpoints embrulham), com as partes
bloqueantes em `asyncio.to_thread` p/ não travar o event loop do uvicorn. Percorre o
MESMO caminho de `run_workflow`/`place_order` do tráfego real (ADR-014).

(O robô de pedidos avulsos da F-017 — `simulate()`/CLI/`POST /api/admin/simulate` — foi
removido na F-018: o simulador de sessões concorrentes o substitui por completo.)
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
    """Carrinho sintético: 1..max_lines SKUs distintos do catálogo, qty 1..max_qty.
    Snapshots dos itens (sku/name/price), como o checkout real envia. Fallback do
    `_weighted_cart` quando nenhuma categoria tem item em estoque."""
    lines = random.randint(1, max_lines)
    picks = random.sample(CATALOG, min(lines, len(CATALOG)))
    return [
        {"sku": p["sku"], "name": p["name"], "qty": random.randint(1, max_qty), "price": p["price"]}
        for p in picks
    ]


# ---------------------------------------------------------------------------
# Engine de sessões concorrentes (F-018)
# ---------------------------------------------------------------------------

# Pool de usuários de teste: nomes EM INGLÊS (CONVENCOES: UI/dados de demo em inglês).
_EN_FIRST = ["Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia", "Mason", "Isabella",
             "Lucas", "Mia", "Logan", "Amelia", "James", "Harper", "Henry", "Ella", "Jack",
             "Grace", "Owen"]
_EN_LAST = ["Carter", "Bennett", "Hayes", "Morgan", "Reed", "Brooks", "Foster", "Sullivan",
            "Parker", "Coleman", "Hughes", "Russell", "Bryant", "Powell", "Ross", "Murphy"]

_SIM_PASSWORD = "sim1234"  # senha fixa do pool de demo (mesma natureza do DEMO; DT-010)
_SIM_STREETS = ["Market St", "Oak Ave", "Maple Rd", "Pine Ln", "Cedar Blvd", "Elm Way", "Bay St"]
_SIM_CITIES = ["Springfield", "Riverton", "Fairview", "Lakeside", "Hillcrest", "Brookfield"]

# Categorias exibidas → tag do catálogo (espelha frontend/lib/shop.ts).
_CATEGORY_TAG = {"Audio": "audio", "Wearables": "wearable", "Home": "casa", "Gifts": "presente"}
_CATEGORIES = list(_CATEGORY_TAG.keys())
_TIERS = ["STANDARD", "GOLD", "PLATINUM"]

# Modos de tráfego (F-039): "api" dirige a camada de serviço in-process (F-018);
# "browser" dirige o navegador real (Playwright) pela UI, p/ o RUM capturar sessões.
_MODES = ["api", "browser"]
# Browser é mais pesado que API → teto de concorrência menor e seguro no modo browser.
_BROWSER_MAX_CONCURRENCY = 12

# Problemas elegíveis p/ injeção por jornada (subconjunto dos toggles do ProblemPanel).
_INJECTABLE = ["price_hallucination", "payment_outage", "inventory_outage", "fraud_false_positive"]

# Pedidos de exemplo p/ o Concierge (UI em inglês). Budget casa com o catálogo.
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
    """Config completa do simulador (todos os knobs vêm da tela). `concurrency` (N) é
    o tamanho do pool E o nº de jornadas simultâneas — um único N (critério da fase)."""
    mode: str = "api"                # api | browser (F-039): API in-process vs navegador real
    concurrency: int = 5
    wait_min_s: float = 1.0          # espera entre jornadas (slot ocioso)
    wait_max_s: float = 4.0
    think_min_s: float = 0.4         # think-time entre ações de navegação
    think_max_s: float = 1.5
    actions_min: int = 2             # nº de ações de navegação antes do checkout
    actions_max: int = 6
    concierge_pct: int = 40          # % de jornadas que usam o AI Concierge
    problem_pct: int = 0             # % de jornadas que injetam um problema no checkout
    problems: list[str] = field(default_factory=lambda: list(_INJECTABLE))
    category_mix: dict[str, int] = field(
        default_factory=lambda: {c: 1 for c in _CATEGORIES})  # peso por categoria no carrinho
    tier_mix: dict[str, int] = field(
        default_factory=lambda: {"STANDARD": 3, "GOLD": 2, "PLATINUM": 1})  # tier dos criados
    speed: float = 1.0               # multiplicador dos sleeps (<1 = demo rápido; 1 = realista)
    target_kind: str = "none"        # none | orders | duration
    target_value: int = 0            # nº de pedidos OU segundos, conforme target_kind
    reset: bool = False              # limpar pedidos antes de iniciar (integra com o Admin)
    max_lines: int = 3               # SKUs distintos por carrinho
    max_qty: int = 2                 # qty máx por linha

    @classmethod
    def from_dict(cls, d: dict | None) -> "SimConfig":
        """Constrói a config a partir do payload da tela, com defaults e clamps."""
        d = d or {}
        cfg = cls()
        cfg.mode = d.get("mode", cfg.mode) if d.get("mode") in _MODES else cfg.mode
        # Browser é pesado → teto menor de concorrência nesse modo (F-039).
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
    """Distribui n itens entre `keys` proporcional aos pesos (resto p/ os maiores pesos)."""
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
    """Garante exatamente N usuários de teste (emails determinísticos → idempotente:
    reusa os existentes, cria os que faltam). Nomes EN, senha fixa, tier distribuído
    (rótulo inicial gravado direto na coluna; o app recomputaria pelo gasto — ADR-008).
    Retorna a lista de usuários do pool."""
    tiers = _distribute(n, tier_mix, _TIERS)
    pool: list[dict] = []
    for i in range(n):
        email = f"sim.shopper{i + 1:02d}@vega.sim"
        user = users.get_user_by_email(email)
        if user is None:
            name = f"{random.choice(_EN_FIRST)} {random.choice(_EN_LAST)}"
            user = users.register(name, email, _SIM_PASSWORD)
            users.update_address(user["id"], _sim_address())
        users.update_tier(user["id"], tiers[i])  # rótulo inicial do tier (distribuição da config)
        pool.append({"id": user["id"], "name": user["name"], "email": email,
                     "address": user.get("address", ""), "tier": tiers[i]})
    return pool


def _weighted_cart(cfg: SimConfig) -> list[dict]:
    """Carrinho com viés de categoria (mix de personas). Sorteia 1..max_lines categorias
    pelos pesos, escolhe um SKU EM ESTOQUE de cada (distintos). Snapshots como o checkout
    real. Se não houver estoque, cai no `random_cart` (a jornada ainda fecha o checkout)."""
    weights = [max(0, cfg.category_mix.get(c, 0)) for c in _CATEGORIES]
    if sum(weights) == 0:
        weights = [1] * len(_CATEGORIES)
    lines = random.randint(1, cfg.max_lines)
    picked: dict[str, dict] = {}
    for _ in range(lines * 3):  # algumas tentativas p/ achar SKUs distintos em estoque
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
    """Engine asyncio de sessões concorrentes. Singleton (`ENGINE`); 1 VM por
    participante, sem multi-tenant. Mantém N workers (slots) que rodam jornadas em
    loop. Estado vivo em memória (painel ao vivo); nada além dos pedidos é persistido."""

    def __init__(self) -> None:
        self.cfg = SimConfig()
        self.status = "stopped"           # stopped | running | paused
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._inject_lock = asyncio.Lock()  # serializa janelas de injeção de problema
        self._browser = None              # BrowserRunner ativo no modo browser (F-039)
        self.pool: list[dict] = []
        # Estado vivo (painel ao vivo):
        self.slots: list[dict] = []       # [{slot, user, action, journeys}]
        self.started_at: float = 0.0      # time.monotonic() do start
        self.started_iso: str | None = None
        self.completed = 0                # pedidos fechados (qualquer status)
        self.paid = 0                     # pedidos PAID
        self.injected = 0                 # jornadas que injetaram um problema (% problema)
        self.by_status: dict[str, int] = {}
        self.errors = 0                   # exceções inesperadas em jornadas

    # --- ciclo de vida -----------------------------------------------------

    async def start(self, cfg: SimConfig) -> dict:
        """(Re)inicia a engine com `cfg`. Para a anterior se houver, opcionalmente
        limpa pedidos (reset), garante o pool de N usuários e sobe N workers + monitor."""
        await self.stop()
        self.cfg = cfg
        if cfg.reset:
            from ..store import orders
            await asyncio.to_thread(orders.clear_all)
        self.pool = await asyncio.to_thread(_ensure_pool, cfg.concurrency, cfg.tier_mix)
        if cfg.mode == "browser":
            from . import sim_browser
            runner = sim_browser.BrowserRunner()
            await runner.start()          # sobe Playwright + Chromium headless (1 por engine)
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
        """Para a engine: sinaliza, aguarda os workers e zera os slots."""
        if self.status == "stopped" and not self._tasks:
            return self.status_dict()
        self.status = "stopped"
        self._stop.set()
        tasks = [t for t in self._tasks if t is not asyncio.current_task()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks = []
        if self._browser is not None:    # fecha o Chromium só depois dos contexts (F-039)
            try:
                await self._browser.stop()
            finally:
                self._browser = None
        for s in self.slots:
            s["user"], s["tier"], s["action"] = None, None, "idle"
        return self.status_dict()

    def pause(self, paused: bool) -> dict:
        """Pausa/retoma. Pausado mantém os workers vivos, porém ociosos."""
        if self.status in ("running", "paused"):
            self.status = "paused" if paused else "running"
        return self.status_dict()

    # --- worker / jornada --------------------------------------------------

    async def _sleep(self, seconds: float) -> None:
        """Sleep cancelável: retorna cedo se a engine for parada."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            pass

    def _scaled(self, lo: float, hi: float) -> float:
        return random.uniform(lo, hi) * self.cfg.speed

    async def _worker(self, slot: dict) -> None:
        """Loop de um slot: sorteia um usuário, roda a jornada, espera, repete —
        mantendo uma jornada ativa neste slot enquanto a engine roda."""
        while self.status != "stopped":
            if self.status == "paused":
                slot["action"] = "paused"
                await self._sleep(0.2)
                continue
            user = random.choice(self.pool)   # sorteio do pool
            slot["user"] = user["name"]
            slot["tier"] = user["tier"]
            try:
                await self._journey(slot, user)
                slot["journeys"] += 1
            except Exception:                  # jornada nunca derruba o worker
                self.errors += 1
            if self.status == "stopped":
                break
            slot["action"] = "waiting"
            await self._sleep(self._scaled(self.cfg.wait_min_s, self.cfg.wait_max_s))
        slot["action"] = "idle"

    async def _journey(self, slot: dict, user: dict) -> None:
        """Uma jornada (modo API ou Browser): login → navega (N ações, concierge opcional)
        → SEMPRE fecha um checkout (compra). Dispatcha pelo modo e contabiliza o resultado
        (PAID/FAILED) num único ponto. `status is None` = engine parou no meio."""
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
        """Modo API (F-018): dirige a camada de serviço in-process (login/concierge/checkout
        reais, partes bloqueantes em `to_thread`). Retorna o status do pedido."""
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
                        pass  # problema injetado (blast radius) na navegação não aborta a compra
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
        """Modo Browser (F-039): dirige o Chromium headless pela UI (Playwright). Reusa a
        config/painel; o think-time e o corte por stop/pause passam pela engine."""
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
        """Liga um toggle de problema só na janela do checkout (serializado p/ não corromper
        o FLAGS global — restaura o valor anterior). Blast radius nos checkouts concorrentes
        é intencional (ADR-014/DT-011). `inject=False` → no-op."""
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
        """Para a engine ao atingir o alvo (nº de pedidos ou duração)."""
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


# --- helpers in-process (rodam em thread; bloqueantes) ----------------------

def _login(email: str) -> str | None:
    """Login real (verifica senha + cria sessão); retorna o token (ou None)."""
    user = users.authenticate(email, _SIM_PASSWORD)
    return users.create_session(user["id"]) if user else None


def _run_concierge() -> None:
    """Roda o workflow do Concierge (mesmo caminho do tráfego real)."""
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
    """Fecha o pedido pelo caminho único e retorna o status."""
    from ..store import checkout
    return checkout.place_order(items, customer, user_id)["status"]


def _browse_label() -> str:
    """Rótulo de uma ação de navegação p/ o painel ao vivo (UI em inglês)."""
    kind = random.choice(["catalog", "category", "search", "detail"])
    if kind == "catalog":
        return "browsing catalog"
    if kind == "category":
        return f"browsing {random.choice(_CATEGORIES)}"
    if kind == "search":
        return f"searching '{random.choice(['headphones', 'watch', 'coffee', 'lamp', 'gift'])}'"
    return f"viewing {random.choice(CATALOG)['name']}"


# Singleton da engine (1 VM por participante).
ENGINE = SimulatorEngine()
