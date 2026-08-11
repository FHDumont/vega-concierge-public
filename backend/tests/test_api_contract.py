"""Contrato da API com o frontend — congelado (CONVENCOES §NÃO mude).

Duas camadas:
  1. o INVENTÁRIO de rotas `{(path, methods)}` — qualquer rota que suma, mude de path ou de
     método quebra aqui. É a rede de segurança do fatiamento de `api.py` em routers;
  2. um punhado de endpoints exercidos de ponta a ponta offline, pra pegar o que o inventário
     não vê (bootstrap que não rodou, dependência que sumiu, payload que mudou de forma).
"""
from __future__ import annotations

import pytest

# Inventário congelado: 64 rotas de `/api/*` + as 4 que o FastAPI publica sozinho.
# Ao ADICIONAR uma rota nova (aditivo, permitido), acrescente a linha aqui no mesmo commit.
FROZEN_ROUTES: set[tuple[str, tuple[str, ...]]] = {
    ("/openapi.json", ("GET", "HEAD")),
    ("/docs", ("GET", "HEAD")),
    ("/docs/oauth2-redirect", ("GET", "HEAD")),
    ("/redoc", ("GET", "HEAD")),
    ("/api/health", ("GET",)),
    ("/api/catalog", ("GET",)),
    ("/api/policies", ("GET",)),
    ("/api/problems", ("GET",)),
    ("/api/problems", ("PUT",)),
    ("/api/problems/preset/{preset_id}", ("POST",)),
    ("/api/galileo/config", ("GET",)),
    ("/api/run", ("POST",)),
    ("/api/chat", ("POST",)),
    ("/api/recommend/gift", ("POST",)),
    ("/api/security/actions", ("POST",)),
    ("/api/product/qa", ("POST",)),
    ("/api/compare", ("POST",)),
    ("/api/cart/crosssell", ("POST",)),
    ("/api/auth/register", ("POST",)),
    ("/api/auth/login", ("POST",)),
    ("/api/auth/logout", ("POST",)),
    ("/api/auth/me", ("GET",)),
    ("/api/auth/me", ("PUT",)),
    ("/api/orders", ("GET",)),
    ("/api/orders", ("POST",)),
    ("/api/orders/{order_id}", ("GET",)),
    ("/api/orders/{order_id}/notification", ("POST",)),
    ("/api/orders/{order_id}/refund", ("POST",)),
    ("/api/orders/{order_id}/fraud-explain", ("POST",)),
    ("/api/account/insights", ("GET",)),
    ("/api/admin/summary", ("GET",)),
    ("/api/admin/insights", ("GET",)),
    ("/api/admin/orders", ("GET",)),
    ("/api/admin/orders", ("DELETE",)),
    ("/api/admin/products", ("GET",)),
    ("/api/admin/seed", ("POST",)),
    ("/api/admin/config/llm-types", ("GET",)),
    ("/api/admin/config/providers", ("GET",)),
    ("/api/admin/config/providers", ("POST",)),
    ("/api/admin/config/providers/{provider_id}", ("PUT",)),
    ("/api/admin/config/providers/{provider_id}", ("DELETE",)),
    ("/api/admin/config/providers/reorder", ("POST",)),
    ("/api/admin/config/providers/{provider_id}/test", ("POST",)),
    ("/api/admin/agents/topology", ("GET",)),
    ("/api/admin/config/agents", ("GET",)),
    ("/api/admin/config/agents/{name}", ("PUT",)),
    ("/api/admin/config/agents/{name}/test", ("POST",)),
    ("/api/admin/config/source", ("GET",)),
    ("/api/admin/config/source", ("PUT",)),
    ("/api/admin/config/source/sync", ("POST",)),
    ("/api/flags", ("GET",)),
    ("/api/admin/flags", ("GET",)),
    ("/api/admin/flags", ("PUT",)),
    ("/api/rum", ("GET",)),
    ("/api/admin/rum", ("GET",)),
    ("/api/admin/rum", ("PUT",)),
    ("/api/hub/config", ("GET",)),
    ("/api/admin/hub/status", ("GET",)),
    ("/api/admin/hub/test-connection", ("POST",)),
    ("/api/admin/enroll", ("POST",)),
    ("/api/admin/hub/enroll-push", ("POST",)),
    ("/api/admin/llm-activity", ("GET",)),
    ("/api/admin/llm-activity", ("DELETE",)),
    ("/api/admin/llm-activity/enabled", ("PUT",)),
    ("/api/simulator/start", ("POST",)),
    ("/api/simulator/stop", ("POST",)),
    ("/api/simulator/pause", ("POST",)),
    ("/api/simulator/status", ("GET",)),
}


def _live_routes() -> set[tuple[str, tuple[str, ...]]]:
    """Rotas efetivamente montadas na app.

    O FastAPI não achata `include_router`: cada router incluído entra em `app.routes` como um
    wrapper que guarda o router original. Por isso a varredura desce por `original_router`.
    """
    from app.api import app

    out: set[tuple[str, tuple[str, ...]]] = set()

    def walk(routes) -> None:
        for route in routes:
            nested = getattr(route, "original_router", None)
            if nested is not None:
                walk(nested.routes)
                continue
            path = getattr(route, "path", None)
            if path is None:
                continue
            out.add((path, tuple(sorted(getattr(route, "methods", None) or []))))

    walk(app.routes)
    return out


def test_route_inventory_has_no_delta():
    live = _live_routes()
    assert not FROZEN_ROUTES - live, f"rotas que sumiram: {sorted(FROZEN_ROUTES - live)}"
    assert not live - FROZEN_ROUTES, f"rotas novas não congeladas: {sorted(live - FROZEN_ROUTES)}"


def test_api_surface_is_sixty_four_routes():
    api_routes = {r for r in _live_routes() if r[0].startswith("/api/")}
    assert len(api_routes) == 64, len(api_routes)


# --- endpoints exercidos offline ---------------------------------------------

def test_health_reports_the_subsystems(api_client):
    body = api_client.get("/api/health").json()
    assert body["status"] == "ok"
    assert set(body) >= {"version", "environment", "rag", "ollama", "llm_providers"}
    assert set(body["rag"]) >= {"enabled", "backend", "embedding_provider"}


def test_catalog_returns_products_with_the_frozen_fields(api_client):
    catalog = api_client.get("/api/catalog").json()
    assert catalog, "catálogo vazio"
    assert set(catalog[0]) >= {"sku", "name", "price", "stock", "tags"}


def test_policies_are_served(api_client):
    assert api_client.get("/api/policies").json()["policies"]


def test_problems_round_trip_through_get_and_put(api_client, reset_problem_flags):
    from tests.conftest import default_flags

    before = api_client.get("/api/problems").json()
    assert set(before) == set(default_flags())

    after = api_client.put("/api/problems", json={"inventory_outage": True}).json()
    assert after["inventory_outage"] is True

    restored = api_client.put("/api/problems", json={"inventory_outage": False}).json()
    assert restored["inventory_outage"] is False


def test_uc_preset_sets_only_its_own_flags(api_client, reset_problem_flags):
    body = api_client.post("/api/problems/preset/uc-2").json()
    assert body["cost_spike"] is True
    assert body["inventory_outage"] is False
    assert body["active_scenario"] == "uc-2"
    assert body["price_hallucination"] is False

    cleared = api_client.post("/api/problems/preset/clear").json()
    assert cleared["active_scenario"] == ""


def test_unknown_preset_is_a_404(api_client):
    assert api_client.post("/api/problems/preset/nope").status_code == 404


def test_run_returns_the_concierge_contract(api_client):
    body = api_client.post("/api/run", json={"request": "a birthday gift under $300"}).json()
    assert body["error"] is None, body["error"]
    assert set(body) == {"messages", "quality", "recommended", "answer", "language", "order", "error"}
    assert body["messages"]


def test_chat_returns_the_chat_contract(api_client):
    body = api_client.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "Are you a bot?"}]},
    ).json()
    assert body["error"] is None, body["error"]
    assert set(body) == {"reply", "intent", "artifacts", "language", "llm_unavailable", "error"}
    assert body["reply"]


def test_chat_account_spend_uses_session_auth(api_client):
    from datetime import datetime, timedelta, timezone

    from app.store import orders, users

    orders.init_db()
    users.init_db()
    users.seed_demo_user()
    user = users.get_user_by_email(users.DEMO_EMAIL)
    assert user, "demo user unavailable"
    user_id = user["id"]
    if not any(
        o["status"] in ("PAID", "SHIPPED", "DELIVERED")
        for o in orders.list_orders_for_user(user_id)
    ):
        customer = {"name": users.DEMO_NAME, "email": users.DEMO_EMAIL, "address": "221B Demo Street"}
        for days_ago, items in users._DEMO_ORDERS:
            total = sum(i["qty"] * i["price"] for i in items)
            created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
            orders.create_order(items, customer, total, status="PAID", user_id=user_id, created_at=created)
    spend = orders.spend_for_user(user_id)
    token = users.create_session(user_id)
    body = api_client.post(
        "/api/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": "Quanto já gastei?"}]},
    ).json()
    assert body["error"] is None, body["error"]
    assert body["intent"] == "stats"
    layout = body.get("artifacts", {}).get("layout") or {}
    fact_values = " ".join(str(f.get("value", "")) for f in layout.get("facts") or [])
    haystack = f"{body['reply']} {fact_values}"
    assert f"{spend:,.2f}" in haystack


def test_flags_are_public(api_client):
    flags = api_client.get("/api/flags").json()
    assert set(flags) >= {"behind_the_scenes", "admin", "simulator", "inspector"}


def test_rum_config_is_public(api_client):
    assert "enabled" in api_client.get("/api/rum").json()


# --- CRUD de providers (owner-gated) ------------------------------------------

@pytest.fixture
def owner_headers() -> dict[str, str]:
    """Sessão de OWNER criada direto no módulo — a senha do owner varia por deploy
    (`OWNER_PASSWORD`), então logar pela API tornaria o teste dependente do ambiente."""
    from app.store import users

    users.seed_owner_user()
    owner = users.get_user_by_email(users.OWNER_EMAIL)
    assert owner, "usuário OWNER não semeado"
    return {"Authorization": f"Bearer {users.create_session(owner['id'])}"}


def test_provider_endpoints_require_a_session(api_client):
    assert api_client.get("/api/admin/config/providers").status_code == 401


def test_provider_crud_round_trip(api_client, owner_headers):
    created = api_client.post(
        "/api/admin/config/providers",
        headers=owner_headers,
        json={"name": "contract-test", "kind": "openai", "base_url": "http://127.0.0.1:1/v1",
              "model": "gpt-4o-mini", "api_key": "sk-contract", "enabled": False},
    ).json()
    provider_id = created["id"]
    try:
        assert "api_key" not in created, "a chave nunca pode voltar ao front"

        listed = api_client.get("/api/admin/config/providers", headers=owner_headers).json()
        assert provider_id in {p["id"] for p in listed}
        assert all("api_key" not in p for p in listed)

        updated = api_client.put(
            f"/api/admin/config/providers/{provider_id}",
            headers=owner_headers, json={"model": "gpt-4o"},
        ).json()
        assert updated["model"] == "gpt-4o"
    finally:
        deleted = api_client.delete(
            f"/api/admin/config/providers/{provider_id}", headers=owner_headers,
        )
        assert deleted.status_code == 200

    assert api_client.put(
        f"/api/admin/config/providers/{provider_id}", headers=owner_headers, json={"model": "x"},
    ).status_code == 404


def test_llm_type_presets_are_owner_gated(api_client, owner_headers):
    assert api_client.get("/api/admin/config/llm-types").status_code == 401
    presets = api_client.get("/api/admin/config/llm-types", headers=owner_headers).json()
    assert {p["type"] for p in presets} >= {"openai", "claude", "bedrock", "custom"}


# --- bootstrap no startup, não no import --------------------------------------

def test_importing_the_app_does_not_bootstrap_anything(monkeypatch):
    """Desde a F-BACKEND-1 o bootstrap mora no `lifespan`. Importar `app.api` — o que
    `fresh-state.sh` e qualquer inspeção de rotas fazem — não pode tocar no SQLite nem
    inicializar o Agent Control."""
    import importlib

    from app import api

    called: list[str] = []
    monkeypatch.setattr(api.orders, "init_db", lambda: called.append("orders"))
    monkeypatch.setattr(api.galileo_control, "init_once", lambda: called.append("control"))

    importlib.reload(api)
    assert called == []


def test_starting_the_app_runs_the_bootstrap(monkeypatch):
    from fastapi.testclient import TestClient

    from app import api

    called: list[str] = []
    monkeypatch.setattr(api, "_bootstrap", lambda: called.append("bootstrap"))
    with TestClient(api.app):
        pass
    assert called == ["bootstrap"]
