"""Business Admin (no auth, governs workshop controls) — sales, products, and seed."""
from fastapi import APIRouter, Header
from ..ai_agents import insights
from ..runnable_config import ai_request_scope
from ..store import admin, orders
from ..store.tools import CATALOG, reset_stock, restore_catalog

# No `prefix`: each route carries the full path, just like it was in `api.py`.
router = APIRouter()


# --- Admin (business layer — owner; F-014) --------------------------------
# Additive endpoints for aggregation/admin (don't change existing contract). Sees ALL
# orders (different from GET /api/orders, scoped by session). Don't require auth —
# consistent with workshop controls (/api/problems), one VM per participant.
# Order details reuse GET /api/orders/{id} (public).

@router.get("/api/admin/summary")
def admin_summary():
    return orders.sales_summary()


# AI-Admin (F-024): sales insights + anomalies + replenishment from AGGREGATED data
# (no raw dumps → cost controlled by cache/max_tokens). Passes through cost control (F-022).
# Honors toggles. Same rules as other /api/admin/* (no auth — workshop controls, VM
# per participant).
@router.get("/api/admin/insights")
def admin_insights(x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="admin_insights", session_id=x_vega_session) as config:
        return insights.admin_insights(config=config)


@router.get("/api/admin/orders")
def admin_orders():
    return orders.list_orders()  # all orders, most recent first


@router.get("/api/admin/products")
def admin_products():
    return [{"sku": p["sku"], "name": p["name"], "price": p["price"],
             "stock": p["stock"], "tags": p["tags"], "deleted": bool(p.get("deleted"))}
            for p in CATALOG]


@router.post("/api/admin/seed")
def admin_seed():
    return {"seeded": admin.seed_sample_orders()}  # populates sample orders (demo)


@router.delete("/api/admin/orders")
def admin_clear():
    # Clear Sales (F-027/F-GALILEO-7): deletes orders, replenishes stock and soft-deletes catalog.
    cleared = orders.clear_all()
    return {
        "cleared": cleared,
        "stock_restored": reset_stock(),
        "catalog_restored": restore_catalog(),
    }
