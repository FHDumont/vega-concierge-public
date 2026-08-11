"""Rate limit compartilhado — janela deslizante stdlib-only (F-WORKSHOP-GUARD).

`RateLimiter` serve HTTP (middleware) e LLM (`llm_cache`). HTTP: bucket por IP + tier de rota;
estoura → 429 com `Retry-After`. LLM: bucket por instância; estoura → stub offline (ADR-016).
"""
from __future__ import annotations

import math
import re
import threading
import time
from typing import Literal

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .settings import settings

Tier = Literal["exempt", "ai", "default"]

_EXEMPT_ROUTES = frozenset({
    ("GET", "/api/health"),
    ("GET", "/api/catalog"),
    ("GET", "/api/policies"),
    ("GET", "/api/flags"),
    ("GET", "/api/rum"),
    ("GET", "/api/galileo/config"),
    ("GET", "/api/hub/config"),
})

_AI_EXACT = frozenset({
    ("POST", "/api/chat"),
    ("POST", "/api/run"),
    ("POST", "/api/recommend/gift"),
    ("POST", "/api/security/actions"),
    ("POST", "/api/product/qa"),
    ("POST", "/api/compare"),
    ("POST", "/api/cart/crosssell"),
    ("GET", "/api/account/insights"),
    ("POST", "/api/orders"),
    ("GET", "/api/admin/insights"),
    ("POST", "/api/simulator/start"),
})

_ORDER_AI_SUFFIX = re.compile(r"^/api/orders/[^/]+/(refund|fraud-explain|notification)$")
_PROVIDER_TEST = re.compile(r"^/api/admin/config/providers/[^/]+/test$")
_AGENT_TEST = re.compile(r"^/api/admin/config/agents/[^/]+/test$")


class RateLimiter:
    """Janela deslizante por bucket. `maxn <= 0` desliga (sempre permite)."""

    def __init__(self, maxn: int, window: float):
        self.maxn = maxn
        self.window = window
        self._hits: list[float] = []
        self._lock = threading.Lock()

    def allow(self) -> bool:
        if self.maxn <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            self._hits = [t for t in self._hits if now - t < self.window]
            if len(self._hits) >= self.maxn:
                return False
            self._hits.append(now)
            return True

    def retry_after(self) -> int:
        """Segundos até a janela liberar o próximo slot (para header Retry-After)."""
        if self.maxn <= 0:
            return 0
        now = time.monotonic()
        with self._lock:
            self._hits = [t for t in self._hits if now - t < self.window]
            if len(self._hits) < self.maxn:
                return 0
            if not self._hits:
                return 1
            oldest = min(self._hits)
            return max(1, int(math.ceil(self.window - (now - oldest))))


_limiter_guard = threading.Lock()
_http_limiters: dict[tuple[str, str], RateLimiter] = {}


def client_ip(request: Request) -> str:
    """IP do cliente para a chave de bucket.

    Com `X-Forwarded-For`, usa o **primeiro** hop (cliente original na cadeia). Atrás de um
    proxy mal configurado ou spoofing do header, o bucket pode não refletir o IP real — aceito
    no workshop (1 VM ≈ 1 participante, acesso direto na :8000).
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def classify_route(method: str, path: str) -> Tier:
    """Classifica rota `/api/*` em tier exempt, ai ou default."""
    method = method.upper()
    path = path.split("?")[0].rstrip("/") or "/"

    if not path.startswith("/api/"):
        return "exempt"

    key = (method, path)
    if key in _EXEMPT_ROUTES:
        return "exempt"
    if key in _AI_EXACT:
        return "ai"
    if method == "POST" and _ORDER_AI_SUFFIX.match(path):
        return "ai"
    if method == "POST" and (_PROVIDER_TEST.match(path) or _AGENT_TEST.match(path)):
        return "ai"
    return "default"


def _tier_limits(tier: str) -> tuple[int, float]:
    if tier == "ai":
        return settings.api_rate_ai_max, settings.api_rate_ai_window_s
    return settings.api_rate_default_max, settings.api_rate_default_window_s


def get_http_limiter(tier: str, ip: str) -> RateLimiter:
    key = (tier, ip)
    with _limiter_guard:
        limiter = _http_limiters.get(key)
        if limiter is None:
            maxn, window = _tier_limits(tier)
            limiter = RateLimiter(maxn, window)
            _http_limiters[key] = limiter
        return limiter


def reset_http_limiters() -> None:
    """Zera buckets HTTP (isolamento entre testes)."""
    with _limiter_guard:
        _http_limiters.clear()


class ApiRateLimitMiddleware(BaseHTTPMiddleware):
    """429 na borda quando o bucket IP+tier estoura; rotas exempt passam sempre."""

    async def dispatch(self, request: Request, call_next):
        if not settings.api_rate_enabled:
            return await call_next(request)
        if request.method.upper() == "OPTIONS":
            return await call_next(request)

        tier = classify_route(request.method, request.url.path)
        if tier == "exempt":
            return await call_next(request)

        limiter = get_http_limiter(tier, client_ip(request))
        if limiter.allow():
            return await call_next(request)

        retry = limiter.retry_after()
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Too many requests. Please wait a moment and try again.",
                "retry_after_seconds": retry,
            },
            headers={"Retry-After": str(retry)},
        )
