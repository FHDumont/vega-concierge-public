"""Admin de NEGÓCIO (sem auth, régua dos controles de workshop) — vendas, produtos e seed."""
from fastapi import APIRouter, Header
from ..ai_agents import insights
from ..runnable_config import ai_request_scope
from ..store import admin, orders
from ..store.tools import CATALOG, reset_stock, restore_catalog

# Sem `prefix`: cada rota carrega o path completo, igualzinho ao que estava em `api.py`.
router = APIRouter()


# --- Admin (camada de NEGÓCIO — dono; F-014) --------------------------------
# Endpoints aditivos de agregação/admin (não mudam o contrato existente). Vê TODOS
# os pedidos (diferente do GET /api/orders, escopado pela sessão). Não exigem auth —
# consistente com os controles de workshop (/api/problems), numa VM por participante.
# O detalhe da ordem reusa GET /api/orders/{id} (público).

@router.get("/api/admin/summary")
def admin_summary():
    return orders.sales_summary()


# IA-Admin (F-024): insights de vendas + anomalias + reposição a partir de dados AGREGADOS
# (não dumps crus → custo controlado por cache/max_tokens). Passa pelo controle de custo (F-022).
# Honra os toggles. Mesma régua dos demais /api/admin/* (sem auth — controles de workshop, VM
# por participante).
@router.get("/api/admin/insights")
def admin_insights(x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="admin_insights", session_id=x_vega_session) as config:
        return insights.admin_insights(config=config)


@router.get("/api/admin/orders")
def admin_orders():
    return orders.list_orders()  # todos os pedidos, mais recentes primeiro


@router.get("/api/admin/products")
def admin_products():
    return [{"sku": p["sku"], "name": p["name"], "price": p["price"],
             "stock": p["stock"], "tags": p["tags"], "deleted": bool(p.get("deleted"))}
            for p in CATALOG]


@router.post("/api/admin/seed")
def admin_seed():
    return {"seeded": admin.seed_sample_orders()}  # popula pedidos de exemplo (demo)


@router.delete("/api/admin/orders")
def admin_clear():
    # Clear Sales (F-027/F-GALILEO-7): apaga pedidos, repõe estoque e soft-deletes do catálogo.
    cleared = orders.clear_all()
    return {
        "cleared": cleared,
        "stock_restored": reset_stock(),
        "catalog_restored": restore_catalog(),
    }
