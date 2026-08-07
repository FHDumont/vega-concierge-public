"""Runner de BROWSER do simulador (F-039) — dirige o navegador real via Playwright.

Diferente do modo API (F-018, dirige a camada de serviço in-process), este runner sobe
um **Chromium headless** e completa a jornada **pela UI do frontend** (login → navegar →
Concierge opcional → add-to-cart → checkout → compra), para que o futuro **RUM** (F-040)
capture sessões de navegador reais. Reusa a config/engine/painel da F-018 (ADR-022): a
`SimulatorEngine` chama `BrowserRunner.run_journey` por jornada quando `cfg.mode == "browser"`.

Playwright/Chromium são **dependência OPCIONAL** (não entram na imagem base — ver
`requirements-browser.txt` + docs): o import é preguiçoso e `available()` reporta a ausência
com instrução de instalação, em vez de quebrar o modo API.
"""
from __future__ import annotations

import random
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

# URL do frontend que o navegador vai dirigir (env; default = dev local).
# Em compose, aponte p/ o serviço do front (ex.: http://frontend:3000).
from ..settings import settings

BASE_URL = settings.sim_browser_base_url
_SIM_PASSWORD = "sim1234"  # mesma senha fixa do pool de demo (espelha simulator.py)
_NAV_TIMEOUT_MS = 20_000   # timeout por ação de navegação/espera


def available() -> tuple[bool, str]:
    """Reporta se o Playwright (Python) está instalado, sem quebrar o modo API.
    Retorna (ok, motivo). `motivo` traz a instrução de instalação quando ausente."""
    try:
        import playwright  # noqa: F401
        return True, ""
    except Exception:
        return False, (
            "Browser mode requires Playwright. Install the optional deps: "
            "`pip install -r requirements-browser.txt && playwright install chromium`."
        )


class BrowserRunner:
    """Dono do ciclo de vida do Playwright + Chromium headless (1 browser por engine).
    Cada jornada roda num **context isolado** (sessão de navegador limpa → RUM fiel)."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self._pw = None        # instância async_playwright
        self._browser = None   # Chromium headless

    async def start(self) -> None:
        """Sobe o Playwright e lança o Chromium headless. Import preguiçoso (opcional)."""
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],  # robustez em container
        )

    async def stop(self) -> None:
        """Fecha o browser e o Playwright (idempotente)."""
        try:
            if self._browser is not None:
                await self._browser.close()
        finally:
            self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None

    async def run_journey(
        self,
        *,
        user: dict,
        cfg,
        set_action: Callable[[str], None],
        think: Callable[[], Awaitable[None]],
        is_stopped: Callable[[], bool],
        checkout_guard,
    ) -> str | None:
        """Uma jornada completa pela UI, terminando SEMPRE no checkout.

        `set_action(label)` alimenta o painel ao vivo (mesmo vocabulário do modo API);
        `think()` aplica o think-time escalado (cancelável); `is_stopped()` corta cedo;
        `checkout_guard` é o context manager da engine que injeta o problema (% problema)
        só na janela do pagamento. Retorna o status do pedido ("PAID"/"FAILED") ou None
        se a engine parou no meio."""
        context = await self._browser.new_context()
        context.set_default_timeout(_NAV_TIMEOUT_MS)
        page = await context.new_page()
        try:
            set_action("signing in")
            await self._login(page, user)
            if is_stopped():
                return None

            n_actions = random.randint(cfg.actions_min, cfg.actions_max)
            await page.goto(f"{self.base_url}/", wait_until="domcontentloaded")
            for _ in range(n_actions):
                if is_stopped():
                    return None
                if random.randint(1, 100) <= cfg.concierge_pct:
                    set_action("asking the concierge")
                    await self._use_concierge(page)
                else:
                    set_action(await self._browse(page))
                await think()

            set_action("checking out")
            return await self._checkout(page, user, checkout_guard)
        finally:
            await context.close()

    # --- passos da jornada ---------------------------------------------------

    async def _login(self, page, user: dict) -> None:
        """Login real pela UI (/account): preenche e-mail/senha e confirma a sessão."""
        await page.goto(f"{self.base_url}/account", wait_until="domcontentloaded")
        await page.locator('input[type="email"]').first.fill(user["email"])
        await page.locator('input[type="password"]').first.fill(_SIM_PASSWORD)
        await page.get_by_role("button", name="Sign in").click()
        # Sucesso = a tela troca p/ o perfil (botão "Sign out" aparece).
        await page.get_by_role("button", name="Sign out").wait_for(timeout=_NAV_TIMEOUT_MS)

    async def _browse(self, page) -> str:
        """Uma ação de navegação pela UI (catálogo/categoria/busca/detalhe).
        Retorna o rótulo p/ o painel (UI em inglês, espelha o modo API)."""
        kind = random.choice(["catalog", "category", "search", "detail"])
        if kind == "catalog":
            await page.goto(f"{self.base_url}/", wait_until="domcontentloaded")
            return "browsing catalog"
        if kind == "category":
            pills = page.locator("button.ns-pill")
            n = await pills.count()
            if n:
                await pills.nth(random.randrange(n)).click()
                return "browsing category"
            return "browsing catalog"
        if kind == "search":
            term = random.choice(["headphones", "watch", "coffee", "lamp", "gift"])
            search = page.locator('input[type="search"]').first
            if await search.count():
                await search.fill(term)
            return f"searching '{term}'"
        # detail: abre o primeiro produto disponível
        link = page.locator('a.name, a.ns-ph').first
        if await link.count():
            await link.click()
            await page.wait_for_load_state("domcontentloaded")
            return "viewing a product"
        return "browsing catalog"

    async def _use_concierge(self, page) -> None:
        """Abre o launcher flutuante do Concierge e faz um pedido (mesmo caminho do tráfego
        real, pois passa pela API). Falha não aborta a compra (best-effort)."""
        prompts = [
            "a birthday gift under $300", "noise-cancelling headphones for travel",
            "a smartwatch for running", "something nice for my kitchen",
            "a present for a coffee lover",
        ]
        try:
            fab = page.locator("button.ns-fab").first
            if not await fab.count():
                return
            if (await fab.get_attribute("aria-expanded")) != "true":
                await fab.click()
            await page.locator("div.ns-fab-panel input.ns-input").first.fill(random.choice(prompts))
            await page.get_by_role("button", name="Ask").click()
            # Espera a resposta (some o "Searching…") sem travar a jornada.
            await page.wait_for_timeout(1500)
            # Fecha o painel p/ não atrapalhar as próximas ações.
            close = page.get_by_role("button", name="Close concierge")
            if await close.count():
                await close.click()
        except Exception:
            pass  # problema injetado / latência no concierge não derruba a jornada

    async def _checkout(self, page, user: dict, checkout_guard) -> str:
        """Garante um item no carrinho e fecha o checkout pela UI (pagamento simulado).
        O `checkout_guard` injeta o problema só na janela do pagamento (% problema)."""
        # 1) add-to-cart: home → primeiro "Add +" disponível.
        await page.goto(f"{self.base_url}/", wait_until="domcontentloaded")
        add = page.locator("button.ns-add:not([disabled])").first
        await add.wait_for(timeout=_NAV_TIMEOUT_MS)
        await add.click()

        # 2) carrinho → checkout (o painel abre ao adicionar).
        checkout_btn = page.get_by_role("button", name="Checkout")
        if await checkout_btn.count():
            await checkout_btn.first.click()
        else:
            await page.goto(f"{self.base_url}/checkout", wait_until="domcontentloaded")
        await page.wait_for_url("**/checkout", timeout=_NAV_TIMEOUT_MS)

        # 3) etapa "Details": nome/e-mail/endereço (endereço pode vir salvo do perfil).
        await page.locator('input[placeholder="Jane Doe"]').first.fill(user["name"])
        await page.locator('input[type="email"]').first.fill(user["email"])
        addr = page.locator('input[placeholder="123 Main St, City"]')
        if await addr.count():
            await addr.first.fill(user.get("address") or "1 Market St, Springfield")
        await page.get_by_role("button", name="Continue to payment").click()

        # 4) etapa "Payment": cartão já vem pré-preenchido; paga (injeção na janela do pay).
        async with checkout_guard():
            await page.get_by_role("button", name="Pay", exact=False).click()
            # 5) confirmação: sucesso (PAID) ou erro (FAILED).
            success = page.locator("div.ns-alert.success")
            failure = page.locator("div.ns-alert.error")
            try:
                await success.or_(failure).first.wait_for(timeout=_NAV_TIMEOUT_MS)
            except Exception:
                return "FAILED"
            return "PAID" if await success.count() else "FAILED"
