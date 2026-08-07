"""UC-4 privileged actions, isolated from concierge and graph orchestration."""
from __future__ import annotations

import json
from typing import Any

from langchain_core.runnables import RunnableLambda

from ..obs import galileo_control
from ..store.tools import delete_product, list_recent_customers
from ..tool_arg_normalize import normalize_sku_arg

DELETE_PRODUCT_STEP_NAME = "delete_product"
LIST_RECENT_CUSTOMERS_STEP_NAME = "list_recent_customers"


def _delete_catalog_product(payload: dict[str, Any]) -> dict:
    """Delete one catalog SKU through Agent Control's ``delete_product`` step."""
    normalized_sku, error = normalize_sku_arg({"sku": payload.get("sku")})
    if error:
        return error
    return galileo_control.controlled_delete_product(
        normalized_sku,
        lambda: delete_product(normalized_sku),
    )


def _export_recent_customers(payload: dict[str, Any]) -> list[dict]:
    """Return the intentionally privileged UC-4 customer export behavior."""
    return list_recent_customers(
        sku=payload.get("sku"),
        limit=payload.get("limit", 5),
    )


delete_product_workflow = RunnableLambda(
    _delete_catalog_product,
    name=DELETE_PRODUCT_STEP_NAME,
)
recent_customers_export_workflow = RunnableLambda(
    _export_recent_customers,
    name=LIST_RECENT_CUSTOMERS_STEP_NAME,
)


def delete_catalog_product(sku: Any = None, *, config=None) -> dict:
    """Run the destructive action through its protected, traced tool boundary."""
    return delete_product_workflow.invoke({"sku": sku}, config=config)


def delete_catalog_product_json(sku: Any = None, *, config=None) -> str:
    """Tool-compatible JSON result for the destructive UC-4 action."""
    return json.dumps(delete_catalog_product(sku, config=config))


def export_recent_customers(
    sku: str | None = None,
    limit: int = 5,
    *,
    config=None,
) -> list[dict]:
    """Run the PII export through its named UC-4 trace boundary."""
    return recent_customers_export_workflow.invoke(
        {"sku": sku, "limit": limit},
        config=config,
    )
