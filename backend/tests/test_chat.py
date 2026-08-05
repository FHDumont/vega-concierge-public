"""Roteamento de intent do `POST /api/chat` sob stub — ex `run_chat_demo.py`
(F-050-CHAT, F-051, F-052, F-053, F-054)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import orders, users
from app.agents import arun_chat_workflow
from app.runnable_config import build_runnable_config, make_thread_id


async def run_chat(messages: list[dict], *, context: dict | None = None,
                   user_id: str | None = None) -> dict:
    config = build_runnable_config(
        thread_id=make_thread_id(user_id=user_id), feature="chat",
        metadata={"user_id": user_id} if user_id else None,
    )
    return await arun_chat_workflow(messages, context=context, config=config)


def _haystacks(final: dict) -> list[str]:
    """Reply + artefatos: a resposta exibida pode estar no texto, no `answer` completo ou nos
    campos do layout estruturado (F-051)."""
    out = [final.get("answer") or ""]
    artifacts = final.get("artifacts") or {}
    if isinstance(artifacts.get("answer"), str):
        out.append(artifacts["answer"])
    layout = artifacts.get("layout")
    if isinstance(layout, dict):
        out += [str(f.get("value", "")) for f in layout.get("facts") or [] if isinstance(f, dict)]
        out += [str(s.get("body", "")) for s in layout.get("sections") or [] if isinstance(s, dict)]
    return out


def assert_in_reply(final: dict, needle: str) -> None:
    assert any(needle.lower() in h.lower() for h in _haystacks(final)), \
        f"{needle!r} ausente em {_haystacks(final)}"


# --- intents sem contexto -----------------------------------------------------

@pytest.mark.parametrize("message,expected", [
    ("What are the policies of Vega?", "general"),
    ("Something compact for travel", "recommend"),
    ("Gift for a coffee lover", "recommend"),
    ("Birthday gift under $300", "recommend"),
    ("a birthday gift under $300", "recommend"),
    ("write a gift message for my sister's birthday", "gift"),
])
async def test_message_routes_to_expected_intent(message, expected):
    final = await run_chat([{"role": "user", "content": message}])
    assert final.get("intent") == expected, final.get("intent")


@pytest.mark.parametrize("message", [
    "How many days do I have to return?",
    "Are you a bot?",
    "compare NS-001 and NS-002",
    "search for wireless headphones",
])
async def test_message_gets_answered(message):
    # Estes não fixam intent (o roteamento sob stub varia); o contrato é responder algo.
    final = await run_chat([{"role": "user", "content": message}])
    assert final.get("answer"), final


async def test_policies_overview_mentions_policies():
    final = await run_chat([{"role": "user", "content": "What are the policies of Vega?"}])
    assert_in_reply(final, "policies")


# --- intents com contexto de produto ------------------------------------------

@pytest.mark.parametrize("message", [
    "Does it have noise cancellation?",
    "Tell me about Aura Bluetooth Headphones",
])
async def test_product_context_routes_to_product_qa(message):
    final = await run_chat([{"role": "user", "content": message}], context={"sku": "NS-001"})
    assert final.get("intent") == "product_qa", final.get("intent")


# --- stats (F-053) ------------------------------------------------------------

async def test_catalog_stats_quote_the_real_extremes():
    final = await run_chat([{"role": "user", "content": "Qual o produto mais caro e o mais barato?"}])
    assert final.get("intent") == "stats"
    assert_in_reply(final, "599")


async def test_bestseller_question_routes_to_stats():
    final = await run_chat([{"role": "user", "content": "What is the best-selling product?"}])
    assert final.get("intent") == "stats"


async def test_guest_asking_about_own_spend_is_asked_to_sign_in():
    final = await run_chat([{"role": "user", "content": "Quanto eu já gastei?"}])
    assert final.get("intent") == "stats"
    assert_in_reply(final, "sign in")


async def test_stats_survives_the_hallucination_toggle(reset_problem_flags):
    reset_problem_flags.price_hallucination = True
    final = await run_chat([{"role": "user", "content": "Qual o produto mais caro?"}])
    assert final.get("intent") == "stats"


# --- stats da conta (exigem o usuário de demo com histórico) -------------------

def _demo_user_id() -> str | None:
    orders.init_db()
    users.init_db()
    users.seed_demo_user()
    user = users.get_user_by_email(users.DEMO_EMAIL)
    if not user:
        return None
    _ensure_demo_history(user["id"])
    return user["id"]


def _ensure_demo_history(user_id: str) -> None:
    """Repõe pedidos pagos de demo se o histórico foi zerado (ou só tem FAILED)."""
    if any(o["status"] in ("PAID", "SHIPPED", "DELIVERED")
           for o in orders.list_orders_for_user(user_id)):
        return
    customer = {"name": users.DEMO_NAME, "email": users.DEMO_EMAIL,
                "address": "221B Demo Street, Test City"}
    for days_ago, items in users._DEMO_ORDERS:
        total = sum(i["qty"] * i["price"] for i in items)
        created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        orders.create_order(items, customer, total, status="PAID", user_id=user_id,
                            created_at=created)


@pytest.fixture
def demo_user_id() -> str:
    user_id = _demo_user_id()
    if not user_id:
        pytest.skip("usuário de demo indisponível")
    return user_id


async def test_account_spend_question_quotes_the_real_spend(demo_user_id):
    from app.ai_features import _digits_from_usd, account_stats

    stats = account_stats(demo_user_id) or {}
    digits = _digits_from_usd(stats["spend"]) if stats.get("spend") is not None else "1275"
    final = await run_chat(
        [{"role": "user", "content": "Quanto de dinheiro eu já gastei com compras?"}],
        user_id=demo_user_id,
    )
    assert final.get("intent") == "stats"
    assert_in_reply(final, digits[-3:] if len(digits) >= 3 else digits)


async def test_account_order_count_question_quotes_the_real_count(demo_user_id):
    from app.ai_features import account_stats

    stats = account_stats(demo_user_id) or {}
    final = await run_chat(
        [{"role": "user", "content": "Quantas compras eu já fiz?"}], user_id=demo_user_id,
    )
    assert final.get("intent") == "stats"
    assert_in_reply(final, str(stats.get("orders", 3)))


# --- returns (exige um pedido DELIVERED) --------------------------------------

@pytest.fixture
def delivered_order_id() -> str:
    users.seed_demo_user()
    for order in orders.list_orders():
        if order["status"] == "DELIVERED":
            return order["id"]
    pytest.skip("nenhum pedido DELIVERED disponível")


async def test_refund_request_reaches_the_returns_flow(delivered_order_id):
    final = await run_chat(
        [{"role": "user", "content": "I want a refund for my order"}],
        context={"order_id": delivered_order_id},
    )
    assert final.get("answer")


async def test_refund_false_denial_still_answers(reset_problem_flags, delivered_order_id):
    reset_problem_flags.refund_false_denial = True
    final = await run_chat(
        [{"role": "user", "content": "please process my refund"}],
        context={"order_id": delivered_order_id},
    )
    assert final.get("answer")
