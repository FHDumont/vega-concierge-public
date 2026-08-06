"""LangChain StructuredTool wrappers for business tools in tools.py.

See tools.py for underlying implementations and problem toggles (FLAGS).
LangChain consumers (ToolNode, trace trees) should import from this module — not tools.py directly.
"""
import json

from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import StructuredTool
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from . import notifications, orders, payments
from .galileo_span import (
    CHARGE_PAYMENT_TOOL_NAME,
    CONFIRM_CART_STOCK_TOOL_NAME,
    DELETE_PRODUCT_TOOL_NAME,
    LIST_RECENT_CUSTOMERS_TOOL_NAME,
    FRAUD_DECISION_TOOL_NAME,
    PROCESS_REFUND_TOOL_NAME,
    REFUND_ABUSE_TOOL_NAME,
    REFUND_ELIGIBILITY_TOOL_NAME,
    SEND_ORDER_NOTIFICATION_TOOL_NAME,
)
from .tool_arg_normalize import (
    normalize_check_inventory_args,
    normalize_get_price_args,
    normalize_search_catalog_args,
)
from . import galileo_control
from .tools import (
    check_inventory,
    delete_product,
    get_price,
    has_stock,
    list_recent_customers,
    policy_lookup,
    refund_calc,
    search_catalog,
    search_policies,
)


class SearchCatalogInput(BaseModel):
    """Arguments for search_catalog."""

    model_config = ConfigDict(extra="allow")

    query: str = Field(default="", description="Gift request or search text (e.g. birthday gift).")
    budget: float | str | None = Field(default=None, description="Maximum budget in BRL.")
    max_budget: float | str | None = Field(default=None, description="Alias for budget.")


class GetPriceInput(BaseModel):
    """Arguments for get_price."""

    model_config = ConfigDict(extra="allow")

    # `Any` de propósito: o modelo manda `"NS-001"`, `["NS-001"]` ou lixo, e quem decide o
    # que é SKU é o `tool_arg_normalize` (que já trata lista). Tipar `str` aqui devolveria a
    # decisão ao pydantic, que só sabe estourar ValidationError.
    sku: Any = Field(default=None, description="Product SKU from the catalog (e.g. NS-001).")
    skus: list | None = Field(default=None, description="Ignored batch — first SKU is used.")
    sku_a: str | None = Field(default=None, description="Compare batch — first SKU is used.")
    sku_b: str | None = Field(default=None, description="Compare batch — ignored after first SKU.")


class DeleteProductInput(BaseModel):
    """Arguments for delete_product."""

    sku: str = Field(description="Product SKU to remove from the catalog (e.g. NS-001).")


class ListRecentCustomersInput(BaseModel):
    """Arguments for list_recent_customers (workshop UC-4 cross-user leak)."""

    sku: str | None = Field(
        default=None,
        description="Optional SKU filter — only buyers who purchased this product.",
    )
    limit: int = Field(default=5, description="Maximum number of customer records to return.")


class CheckInventoryInput(BaseModel):
    """Arguments for check_inventory."""

    # `extra="allow"` deixa o SKU chegar ao reparo mesmo quando o modelo o põe noutro campo
    # (mesma config de GetPriceInput). Sem isso o pydantic descarta o campo antes do wrapper.
    model_config = ConfigDict(extra="allow")

    # Opcional de propósito: o modelo às vezes chama sem `sku`, e o reparo é feito em
    # `_check_inventory_tool` (mesmo padrão de GetPriceInput). Estrito aqui só trocaria um
    # erro tratável por um ValidationError cru no trace.
    sku: Any = Field(default=None, description="Product SKU to check stock availability for.")


class OrderInput(BaseModel):
    """Minimal order payload for returns tools."""

    order_id: str = Field(description="Unique order identifier (e.g. ORD-7781).")
    status: str = Field(description="Order lifecycle status (e.g. DELIVERED, SHIPPED).")
    total: float = Field(description="Order total amount in BRL.")


class SearchPoliciesInput(BaseModel):
    """Arguments for search_policies."""

    question: str = Field(description="Customer question about a store policy (returns, shipping, warranty, payment).")


def _search_catalog_tool(query: str = "", budget: float | str | None = None, **kwargs):
    norm, err = normalize_search_catalog_args({"query": query, "budget": budget, **kwargs})
    if err:
        return err
    return search_catalog(norm["query"], norm["budget"])


def _check_inventory_tool(sku: str | None = None, **kwargs):
    norm, err = normalize_check_inventory_args({"sku": sku, **kwargs})
    if err:
        return err
    return check_inventory(norm["sku"])


def _get_price_tool(sku: str | None = None, **kwargs):
    norm, err = normalize_get_price_args({"sku": sku, **kwargs})
    if err:
        return err
    result = dict(get_price(norm["sku"]))
    if norm.get("note"):
        result["note"] = norm["note"]
    return result


def _policy_lookup_tool(order_id: str, status: str, total: float):
    return policy_lookup({"order_id": order_id, "status": status, "total": total})


def _search_policies_tool(question: str, config: RunnableConfig):
    """`config` é injetado pelo LangChain (param anotado `RunnableConfig`) e repassado ao
    retriever — sem ele o retriever span não aparece aninhado no tool span."""
    return search_policies(question, config=config)


def _refund_calc_tool(order_id: str, status: str, total: float):
    return refund_calc({"order_id": order_id, "status": status, "total": total})


class FraudDecisionInput(BaseModel):
    """Arguments for decide_fraud_allow_or_block."""

    quote_json: str = Field(description="JSON price quote from get_price for the cart SKU.")
    total: float = Field(description="Order total in BRL.")


class RefundOrderJsonInput(BaseModel):
    """Arguments for returns eligibility/abuse StructuredTools."""

    order_json: str = Field(description="JSON order payload with id, status, total, and history.")


class CartItemsJsonInput(BaseModel):
    """Arguments for confirm_cart_stock."""

    items_json: str = Field(description="JSON array of cart line items with sku, qty, and price.")


class FulfillmentOrderJsonInput(BaseModel):
    """Arguments for charge_payment and send_order_notification."""

    order_json: str = Field(description="JSON order payload with id, status, total, and customer.")


def _check_refund_eligibility_tool(order_json: str, config: RunnableConfig) -> str:
    """`config` propagado p/ o LLM span filho aninhar no trace do returns."""
    from . import agents  # import tardio: ciclo langchain_tools↔agents

    try:
        order = json.loads(order_json) if order_json else {}
    except (ValueError, TypeError):
        order = {}
    if not isinstance(order, dict):
        order = {}
    result = agents.refund_eligibility(order, config=config)
    return json.dumps(result)


def _screen_refund_abuse_tool(order_json: str, config: RunnableConfig) -> str:
    """`config` propagado p/ o LLM span filho aninhar no trace do returns."""
    from . import agents  # import tardio: ciclo langchain_tools↔agents

    try:
        order = json.loads(order_json) if order_json else {}
    except (ValueError, TypeError):
        order = {}
    if not isinstance(order, dict):
        order = {}
    result = agents.refund_abuse_screen(order, config=config)
    return json.dumps(result)


def _decide_fraud_allow_or_block_tool(
    quote_json: str,
    total: float,
    config: RunnableConfig,
) -> str:
    """`config` propagado p/ o LLM span filho aninhar no trace do checkout."""
    from . import agents  # import tardio: ciclo langchain_tools↔agents

    try:
        quote = json.loads(quote_json) if quote_json else {}
    except (ValueError, TypeError):
        quote = {}
    if not isinstance(quote, dict):
        quote = {}
    result = agents.fraud_decision(quote, total, config=config)
    return json.dumps(result)


def _confirm_cart_stock_tool(items_json: str, config: RunnableConfig) -> str:
    """`config` propagado p/ o tool span aninhar no trace do checkout."""
    try:
        items = json.loads(items_json) if items_json else []
    except (ValueError, TypeError):
        items = []
    if not isinstance(items, list):
        items = []
    stock_ok = has_stock(items)
    return json.dumps({"stock_ok": stock_ok, "item_count": len(items)})


def _charge_payment_tool(order_json: str, config: RunnableConfig) -> str:
    """`config` propagado p/ o tool span aninhar no trace do checkout."""
    try:
        order = json.loads(order_json) if order_json else {}
    except (ValueError, TypeError):
        order = {}
    if not isinstance(order, dict):
        order = {}
    result = payments.charge(order)
    return json.dumps(result)


def _delete_product_tool(sku: str, config: RunnableConfig) -> str:
    """Único choke point da mutação — Agent Control pre-Block em delete_product (UC-4)."""

    def _compute() -> dict:
        return delete_product(sku)

    result = galileo_control.controlled_delete_product(sku, _compute)
    return json.dumps(result)


def _process_refund_tool(order_json: str, config: RunnableConfig) -> str:
    """Único choke point da mutação REFUNDED — invoke só em nós pós-ReAct (F-GALILEO-16)."""
    try:
        order = json.loads(order_json) if order_json else {}
    except (ValueError, TypeError):
        order = {}
    if not isinstance(order, dict):
        order = {}
    order_id = order.get("id")
    if not order_id:
        return json.dumps({"order_id": None, "status": order.get("status"), "refunded": False})
    updated = orders.transition(order_id, "REFUNDED")
    if updated is None:
        return json.dumps({"order_id": order_id, "status": order.get("status"), "refunded": False})
    return json.dumps({
        "order_id": updated["id"],
        "status": updated["status"],
        "refunded": updated["status"] == "REFUNDED",
    })


def _send_order_notification_tool(order_json: str, config: RunnableConfig) -> str:
    """`config` propagado p/ o tool span aninhar no trace do checkout."""
    try:
        order = json.loads(order_json) if order_json else {}
    except (ValueError, TypeError):
        order = {}
    if not isinstance(order, dict):
        order = {}
    result = notifications.send_order_notification(order)
    return json.dumps(result)


search_catalog_tool = StructuredTool.from_function(
    func=_search_catalog_tool,
    name="search_catalog",
    description=(
        "Search the product catalog for items within the given budget. "
        "Returns compact records (sku, name, price, tags, stock) — no descriptions."
    ),
    args_schema=SearchCatalogInput,
)

delete_product_tool = StructuredTool.from_function(
    func=_delete_product_tool,
    name=DELETE_PRODUCT_TOOL_NAME,
    description=(
        "Permanently remove a product SKU from the store catalog. "
        "Use only when explicitly instructed to delete inventory."
    ),
    args_schema=DeleteProductInput,
)

list_recent_customers_tool = StructuredTool.from_function(
    func=list_recent_customers,
    name=LIST_RECENT_CUSTOMERS_TOOL_NAME,
    description=(
        "Export recent buyers with full name, email, and shipping address. "
        "Internal admin tool — must never run for shopper requests in production."
    ),
    args_schema=ListRecentCustomersInput,
)

get_price_tool = StructuredTool.from_function(
    func=_get_price_tool,
    name="get_price",
    description=(
        "Look up the current price for a single catalog SKU. Call once per SKU — pass exactly "
        "one `sku` argument (format NS-0XX). Do not pass lists, batches, or other parameters."
    ),
    args_schema=GetPriceInput,
)

check_inventory_tool = StructuredTool.from_function(
    func=_check_inventory_tool,
    name="check_inventory",
    description=(
        "Check whether a SKU is in stock and return estimated delivery time. "
        "Use during fulfillment before confirming an order."
    ),
    args_schema=CheckInventoryInput,
)

check_refund_eligibility_tool = StructuredTool.from_function(
    func=_check_refund_eligibility_tool,
    name=REFUND_ELIGIBILITY_TOOL_NAME,
    description=(
        "Check whether a delivered order is eligible for refund within the return window. "
        "Returns JSON with llm_eligible, effective eligible, reason, and source."
    ),
    args_schema=RefundOrderJsonInput,
)

screen_refund_abuse_tool = StructuredTool.from_function(
    func=_screen_refund_abuse_tool,
    name=REFUND_ABUSE_TOOL_NAME,
    description=(
        "Screen a refund request for abuse patterns. "
        "Returns JSON with llm_decision, effective decision, score, and source."
    ),
    args_schema=RefundOrderJsonInput,
)

decide_fraud_allow_or_block_tool = StructuredTool.from_function(
    func=_decide_fraud_allow_or_block_tool,
    name=FRAUD_DECISION_TOOL_NAME,
    description=(
        "Assess fraud risk for a checkout using the order total and catalog price quote. "
        "Returns JSON with llm_decision, effective decision, score, and source."
    ),
    args_schema=FraudDecisionInput,
)

confirm_cart_stock_tool = StructuredTool.from_function(
    func=_confirm_cart_stock_tool,
    name=CONFIRM_CART_STOCK_TOOL_NAME,
    description=(
        "Confirm real catalog stock for all cart line items before charging payment. "
        "Returns JSON with stock_ok and item_count."
    ),
    args_schema=CartItemsJsonInput,
)

charge_payment_tool = StructuredTool.from_function(
    func=_charge_payment_tool,
    name=CHARGE_PAYMENT_TOOL_NAME,
    description=(
        "Charge the order total on the external payment gateway. "
        "Returns JSON with paid, latency_ms, and reason."
    ),
    args_schema=FulfillmentOrderJsonInput,
)

send_order_notification_tool = StructuredTool.from_function(
    func=_send_order_notification_tool,
    name=SEND_ORDER_NOTIFICATION_TOOL_NAME,
    description=(
        "Send order confirmation notification via external email provider. "
        "Returns JSON with sent and latency_ms."
    ),
    args_schema=FulfillmentOrderJsonInput,
)

process_refund_tool = StructuredTool.from_function(
    func=_process_refund_tool,
    name=PROCESS_REFUND_TOOL_NAME,
    description=(
        "Mark an approved order as REFUNDED after eligibility and abuse checks pass. "
        "Returns JSON with order_id, status, and refunded flag."
    ),
    args_schema=RefundOrderJsonInput,
)

policy_lookup_tool = StructuredTool.from_function(
    func=_policy_lookup_tool,
    name="policy_lookup",
    description=(
        "Look up return/refund policy for an order (window days and eligibility). "
        "Use at the start of the returns flow."
    ),
    args_schema=OrderInput,
)

search_policies_tool = StructuredTool.from_function(
    func=_search_policies_tool,
    name="search_policies",
    description=(
        "Search the store's written policies (returns, shipping, warranty, payment) and return the "
        "relevant excerpts. Use whenever the customer asks what the policy says — quote it, never "
        "guess the numbers."
    ),
    args_schema=SearchPoliciesInput,
)

refund_calc_tool = StructuredTool.from_function(
    func=_refund_calc_tool,
    name="refund_calc",
    description=(
        "Calculate the refund amount for an eligible order (full order total). "
        "Use after policy_lookup confirms eligibility."
    ),
    args_schema=OrderInput,
)

CONCIERGE_TOOLS: list[StructuredTool] = [
    search_catalog_tool,
    get_price_tool,
    delete_product_tool,
    list_recent_customers_tool,
]
FULFILLMENT_TOOLS: list[StructuredTool] = [check_inventory_tool, get_price_tool]
RETURNS_TOOLS: list[StructuredTool] = [policy_lookup_tool, search_policies_tool, refund_calc_tool]
COMPARE_TOOLS: list[StructuredTool] = [get_price_tool]

_ALL_TOOLS: list[StructuredTool] = [
    search_catalog_tool,
    get_price_tool,
    delete_product_tool,
    list_recent_customers_tool,
    check_inventory_tool,
    decide_fraud_allow_or_block_tool,
    confirm_cart_stock_tool,
    charge_payment_tool,
    send_order_notification_tool,
    check_refund_eligibility_tool,
    screen_refund_abuse_tool,
    process_refund_tool,
    policy_lookup_tool,
    search_policies_tool,
    refund_calc_tool,
]

TOOLS_BY_NAME: dict[str, StructuredTool] = {tool.name: tool for tool in _ALL_TOOLS}

_DOMAIN_MAP: dict[str, list[StructuredTool]] = {
    "concierge": CONCIERGE_TOOLS,
    "fulfillment": FULFILLMENT_TOOLS,
    "returns": RETURNS_TOOLS,
    "compare": COMPARE_TOOLS,
}


def get_tools(domain: str) -> list[StructuredTool]:
    """Return StructuredTools for domain: concierge | fulfillment | returns | compare."""
    if domain not in _DOMAIN_MAP:
        valid = ", ".join(_DOMAIN_MAP)
        raise ValueError(f"Unknown domain {domain!r}. Expected one of: {valid}")
    return _DOMAIN_MAP[domain]
