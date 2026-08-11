"""Simulator BROWSER runner (F-039) — drives real browser via Playwright.

Unlike API mode (F-018, drives in-process service layer), this runner launches
**headless Chromium** and completes journey **via frontend UI** (login → browse →
optional Concierge → add-to-cart → checkout → purchase), so future **RUM** (F-040)
captures real browser sessions. Reuses config/engine/panel from F-018 (ADR-022):
`SimulatorEngine` calls `BrowserRunner.run_journey` per journey when `cfg.mode == "browser"`.

Playwright/Chromium are **OPTIONAL dependency** (don't enter base image — see
`requirements-browser.txt` + docs): import is lazy and `available()` reports absence
with installation instructions, instead of breaking API mode.
"""
from __future__ import annotations

import random
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

# Frontend URL the browser will drive (env; default = local dev).
# In compose, point to front service (e.g., http://frontend:3000).
from ..settings import settings

BASE_URL = settings.sim_browser_base_url
_SIM_PASSWORD = "sim1234"  # same fixed password as demo pool (mirrors simulator.py)
_NAV_TIMEOUT_MS = 20_000   # timeout per navigation/wait action


def available() -> tuple[bool, str]:
    """Report if Playwright (Python) is installed, without breaking API mode.
    Returns (ok, reason). `reason` brings installation instruction when absent."""
    try:
        import playwright  # noqa: F401
        return True, ""
    except Exception:
        return False, (
            "Browser mode requires Playwright. Install the optional deps: "
            "`pip install -r requirements-browser.txt && playwright install chromium`."
        )


class BrowserRunner:
    """Owner of Playwright + headless Chromium lifecycle (1 browser per engine).
    Each journey runs in **isolated context** (clean browser session → faithful RUM)."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self._pw = None        # async_playwright instance
        self._browser = None   # Chromium headless

    async def start(self) -> None:
        """Launch Playwright and start headless Chromium. Lazy import (optional)."""
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],  # robustness in containers
        )

    async def stop(self) -> None:
        """Close browser and Playwright (idempotent)."""
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
        """Complete journey via UI, always ending at checkout.

        `set_action(label)` feeds live panel (same vocabulary as API mode);
        `think()` applies scaled think-time (cancellable); `is_stopped()` cuts early;
        `checkout_guard` is engine context manager injecting problem (% problem)
        only in payment window. Returns order status ("PAID"/"FAILED") or None
        if engine stopped mid-way."""
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

    # --- journey steps -------------------------------------------------------

    async def _login(self, page, user: dict) -> None:
        """Real login via UI (/account): fills email/password and confirms session."""
        await page.goto(f"{self.base_url}/account", wait_until="domcontentloaded")
        await page.locator('input[type="email"]').first.fill(user["email"])
        await page.locator('input[type="password"]').first.fill(_SIM_PASSWORD)
        await page.get_by_role("button", name="Sign in").click()
        # Success = screen switches to profile (Sign out button appears).
        await page.get_by_role("button", name="Sign out").wait_for(timeout=_NAV_TIMEOUT_MS)

    async def _browse(self, page) -> str:
        """One UI navigation action (catalog/category/search/detail).
        Returns label for panel (UI in English, mirrors API mode)."""
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
        # detail: open first available product
        link = page.locator('a.name, a.ns-ph').first
        if await link.count():
            await link.click()
            await page.wait_for_load_state("domcontentloaded")
            return "viewing a product"
        return "browsing catalog"

    async def _use_concierge(self, page) -> None:
        """Open Concierge floating launcher and make request (same path as real traffic,
        goes through API). Failure doesn't abort purchase (best-effort)."""
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
            # Wait for response (Searching… disappears) without hanging journey.
            await page.wait_for_timeout(1500)
            # Close panel to not interfere with next actions.
            close = page.get_by_role("button", name="Close concierge")
            if await close.count():
                await close.click()
        except Exception:
            pass  # injected problem / latency in the concierge doesn't abort the journey

    async def _checkout(self, page, user: dict, checkout_guard) -> str:
        """Ensure item in cart and close checkout via UI (simulated payment).
        `checkout_guard` injects problem only in payment window (% problem)."""
        # 1) add-to-cart: home → first available "Add +".
        await page.goto(f"{self.base_url}/", wait_until="domcontentloaded")
        add = page.locator("button.ns-add:not([disabled])").first
        await add.wait_for(timeout=_NAV_TIMEOUT_MS)
        await add.click()

        # 2) cart → checkout (panel opens on add).
        checkout_btn = page.get_by_role("button", name="Checkout")
        if await checkout_btn.count():
            await checkout_btn.first.click()
        else:
            await page.goto(f"{self.base_url}/checkout", wait_until="domcontentloaded")
        await page.wait_for_url("**/checkout", timeout=_NAV_TIMEOUT_MS)

        # 3) "Details" stage: name/email/address (address may be saved from profile).
        await page.locator('input[placeholder="Jane Doe"]').first.fill(user["name"])
        await page.locator('input[type="email"]').first.fill(user["email"])
        addr = page.locator('input[placeholder="123 Main St, City"]')
        if await addr.count():
            await addr.first.fill(user.get("address") or "1 Market St, Springfield")
        await page.get_by_role("button", name="Continue to payment").click()

        # 4) "Payment" stage: card already pre-filled; pays (injection in pay window).
        async with checkout_guard():
            await page.get_by_role("button", name="Pay", exact=False).click()
            # 5) confirmation: success (PAID) or error (FAILED).
            success = page.locator("div.ns-alert.success")
            failure = page.locator("div.ns-alert.error")
            try:
                await success.or_(failure).first.wait_for(timeout=_NAV_TIMEOUT_MS)
            except Exception:
                return "FAILED"
            return "PAID" if await success.count() else "FAILED"
