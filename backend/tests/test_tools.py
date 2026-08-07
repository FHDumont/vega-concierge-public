"""Production contracts for store tools and their public AI routes."""
from __future__ import annotations

import json

import pytest

from app.store.langchain_tools import TOOLS_BY_NAME, get_tools
from app.store.tools import CATALOG, delete_product, restore_catalog, search_catalog
from tests.spans import SpanSpy

SAMPLE_ORDER = {"order_id": "ORD-7781", "status": "DELIVERED", "total": 249.0}
MINIMAL_INPUTS = {
    "search_catalog": {"query": "birthday gift", "budget": 300.0},
    "get_price": {"sku": "NS-001"},
    "delete_product": {"sku": "NS-001"},
    "list_recent_customers": {"sku": "NS-001", "limit": 3},
    "check_inventory": {"sku": "NS-001"},
    "policy_lookup": SAMPLE_ORDER,
    "search_policies": {"question": "how many days do I have to return an order?"},
    "refund_calc": SAMPLE_ORDER,
}


@pytest.fixture
def pristine_catalog():
    restore_catalog()
    yield
    restore_catalog()


@pytest.mark.parametrize("name", sorted(MINIMAL_INPUTS))
def test_tool_invokes_with_minimal_input(name, pristine_catalog):
    assert TOOLS_BY_NAME[name].invoke(MINIMAL_INPUTS[name]) is not None


@pytest.mark.parametrize("domain", ["concierge", "fulfillment", "returns", "compare"])
def test_get_tools_returns_a_domain_catalog(domain):
    assert get_tools(domain), f"domínio {domain} sem tools"


def test_get_tools_rejects_unknown_domain():
    with pytest.raises(ValueError):
        get_tools("invalid")


def test_search_policies_emits_a_retriever_span_and_finds_the_return_window():
    spy = SpanSpy()
    result = TOOLS_BY_NAME["search_policies"].invoke(
        MINIMAL_INPUTS["search_policies"], config={"callbacks": [spy]},
    )
    assert spy.retriever_queries, "nenhum retriever span emitido"
    assert "Return window" in [chunk["section"] for chunk in result["chunks"]]


def test_delete_product_soft_deletes_and_restore_puts_it_back(pristine_catalog):
    raw = TOOLS_BY_NAME["delete_product"].invoke({"sku": "NS-001"})
    result = json.loads(raw) if isinstance(raw, str) else raw
    assert result.get("deleted") is True
    assert not any(product["sku"] == "NS-001" for product in search_catalog("", 9999.0))
    assert restore_catalog() == 1
    assert not any(product.get("deleted") for product in CATALOG if product["sku"] == "NS-001")


def test_public_product_qa_preserves_grounding_and_retrieval(api_client):
    body = api_client.post(
        "/api/product/qa", json={"sku": "NS-001", "question": "how many days to return this?"},
    ).json()
    assert body["answer"]
    assert body["grounded"] is True


def test_public_product_qa_marks_the_hallucination_scenario_ungrounded(api_client, reset_problem_flags):
    reset_problem_flags.price_hallucination = True
    body = api_client.post(
        "/api/product/qa", json={"sku": "NS-001", "question": "how much does it cost?"},
    ).json()
    assert body["answer"]
    assert body["grounded"] is False
    assert "249" not in body["answer"]


def test_product_qa_layout_omits_catalog_price_when_ungrounded():
    from app.chat_layout import build_product_qa_layout
    from app.store.tools import CATALOG

    product = next(p for p in CATALOG if p["sku"] == "NS-001")
    invented = "Absolutely — it's on a special deal at just $9.90 today."
    layout = build_product_qa_layout(product, invented, question="how much?", grounded=False)
    assert layout
    price_facts = [f for f in layout.get("facts") or [] if f.get("label") == "Price"]
    assert len(price_facts) == 1
    assert price_facts[0]["value"] == "$9.90"
    assert "249" not in price_facts[0]["value"]


def test_public_security_route_executes_the_uc4_delete_path(api_client, reset_problem_flags, pristine_catalog):
    reset_problem_flags.prompt_injection = True
    body = api_client.post(
        "/api/security/actions",
        json={"action": "delete_product", "sku": "NS-001"},
    ).json()
    assert body["deleted"] is True
    assert not any(product["sku"] == "NS-001" for product in search_catalog("", 9999.0))


def test_public_notification_route_preserves_the_ungrounded_pii_scenario(api_client, reset_problem_flags):
    from app.store import orders

    orders.init_db()
    product = CATALOG[0]
    order = orders.create_order(
        [{"sku": product["sku"], "name": product["name"], "qty": 1, "price": product["price"]}],
        {"name": "Demo User", "email": "demo@vega.test", "address": "221B Demo Street",
         "ssn": "123-45-6789", "card_number": "4242 4242 4242 4242",
         "card_exp": "08/28", "card_cvv": "123"},
        product["price"], "DELIVERED",
    )
    reset_problem_flags.price_hallucination = True
    body = api_client.post(f"/api/orders/{order['id']}/notification").json()
    assert body["grounded"] is False
    assert "123-45-6789" in body["body"]
    assert "4242" in body["body"]


MALFORMED_ARGS = [
    {}, {"sku": None}, {"sku": "lixo"}, {"sku": 42}, {"sku": ["NS-004"]},
    {"product": "NS-003"}, {"skus": ["NS-005", "NS-001"]},
]


@pytest.mark.parametrize("tool_name", ["check_inventory", "get_price"])
@pytest.mark.parametrize("args", MALFORMED_ARGS, ids=str)
def test_sku_tool_never_raises_on_malformed_args(tool_name, args):
    assert isinstance(TOOLS_BY_NAME[tool_name].invoke(args), dict)


@pytest.mark.parametrize("tool_name", ["check_inventory", "get_price"])
@pytest.mark.parametrize(("args", "expected"), [
    ({"sku": "ns-002"}, "NS-002"), ({"sku": ["NS-004"]}, "NS-004"),
])
def test_sku_tool_recovers_a_findable_sku(tool_name, args, expected):
    assert TOOLS_BY_NAME[tool_name].invoke(args)["sku"] == expected


@pytest.mark.parametrize("tool_name", ["check_inventory", "get_price"])
def test_sku_tool_reports_a_usable_hint_when_there_is_no_sku(tool_name):
    result = TOOLS_BY_NAME[tool_name].invoke({})
    assert result["ok"] is False
    assert result["error"] == "invalid_sku"
    assert "NS-001" in result["hint"]


@pytest.mark.parametrize("args", MALFORMED_ARGS, ids=str)
def test_delete_product_never_raises_on_malformed_args(args, pristine_catalog):
    result = json.loads(TOOLS_BY_NAME["delete_product"].invoke(args))
    assert isinstance(result, dict)


@pytest.mark.parametrize("args", [{}, {"question": ""}, {"question": None}])
def test_search_policies_reports_missing_question_instead_of_raising(args):
    result = TOOLS_BY_NAME["search_policies"].invoke(args)
    assert result["ok"] is False
    assert result["error"] == "missing_question"


@pytest.mark.parametrize("key", ["question", "query", "q", "text", "input"])
def test_search_policies_accepts_common_question_synonyms(key):
    assert "chunks" in TOOLS_BY_NAME["search_policies"].invoke({key: "what is your return policy?"})


@pytest.mark.parametrize("args", [{}, {"order_id": "ORD-1"}, {"status": ""}, {"status": None}])
def test_policy_lookup_reports_missing_status_instead_of_raising(args):
    result = TOOLS_BY_NAME["policy_lookup"].invoke(args)
    assert result["ok"] is False
    assert result["error"] == "missing_status"


@pytest.mark.parametrize("args", [{}, {"order_id": "ORD-1"}, {"total": None}, {"total": "n/a"}])
def test_refund_calc_reports_missing_total_instead_of_raising(args):
    result = TOOLS_BY_NAME["refund_calc"].invoke(args)
    assert result["ok"] is False
    assert result["error"] == "missing_total"
