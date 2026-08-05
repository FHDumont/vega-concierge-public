"""Smoke demo for POST /api/chat — intents via stub offline routing (F-050-CHAT, F-051, F-052, F-053)."""
import asyncio
import os
import sys

os.environ.setdefault("DEPLOYMENT_ENVIRONMENT", "user-42")

from app.agents import arun_chat_workflow
from app import orders, users
from app.problems import FLAGS
from app.runnable_config import build_runnable_config, make_thread_id


async def run_chat(label: str, messages: list[dict], *, context: dict | None = None,
                   user_id: str | None = None, expect_intent: str | None = None,
                   expect_in_reply: str | None = None):
    print(f"\n===== CHAT: {label} =====", file=sys.stderr)
    meta = {"user_id": user_id} if user_id else None
    config = build_runnable_config(
        thread_id=make_thread_id(user_id=user_id), feature="chat", metadata=meta,
    )
    final = await arun_chat_workflow(messages, context=context, config=config)
    intent = final.get("intent")
    reply = final.get("answer") or ""
    print("INTENT:", intent, file=sys.stderr)
    print("REPLY:", reply, file=sys.stderr)
    artifacts = final.get("artifacts") or {}
    print("ARTIFACTS keys:", list(artifacts.keys()), file=sys.stderr)
    if expect_intent and intent != expect_intent:
        print(f"  FAIL: expected intent {expect_intent!r}, got {intent!r}", file=sys.stderr)
        raise SystemExit(1)
    if expect_in_reply:
        haystacks = [reply]
        full = artifacts.get("answer")
        if isinstance(full, str):
            haystacks.append(full)
        layout = artifacts.get("layout")
        if isinstance(layout, dict):
            for fact in layout.get("facts") or []:
                if isinstance(fact, dict):
                    haystacks.append(str(fact.get("value", "")))
            for sec in layout.get("sections") or []:
                if isinstance(sec, dict):
                    haystacks.append(str(sec.get("body", "")))
        if not any(expect_in_reply.lower() in (h or "").lower() for h in haystacks):
            print(f"  FAIL: expected {expect_in_reply!r} in reply/layout", file=sys.stderr)
            raise SystemExit(1)
    if final.get("intent") == "returns":
        print("  approved:", artifacts.get("approved"), file=sys.stderr)
        print("  refunded:", artifacts.get("refunded"), file=sys.stderr)
    if final.get("intent") == "stats":
        print("  scopes:", artifacts.get("scopes"), file=sys.stderr)
        print("  grounded:", artifacts.get("grounded"), file=sys.stderr)
    for m in final.get("trace", []):
        print("  -", m, file=sys.stderr)


def _ensure_demo_history(user_id: str) -> None:
    """Garante pedidos pagos de demo se o histórico foi zerado ou só tem FAILED."""
    from datetime import datetime, timedelta, timezone

    paid_statuses = ("PAID", "SHIPPED", "DELIVERED")
    user_orders = orders.list_orders_for_user(user_id)
    if any(o["status"] in paid_statuses for o in user_orders):
        return
    customer = {"name": users.DEMO_NAME, "email": users.DEMO_EMAIL, "address": "221B Demo Street, Test City"}
    for days_ago, items in users._DEMO_ORDERS:
        total = sum(i["qty"] * i["price"] for i in items)
        created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        orders.create_order(items, customer, total, status="PAID", user_id=user_id, created_at=created)


def _demo_user_id() -> str | None:
    orders.init_db()
    users.init_db()
    users.seed_demo_user()
    user = users.get_user_by_email(users.DEMO_EMAIL)
    if user:
        _ensure_demo_history(user["id"])
    return user["id"] if user else None


def _demo_delivered_order_id() -> str | None:
    users.seed_demo_user()
    for order in orders.list_orders():
        if order["status"] == "DELIVERED":
            return order["id"]
    return None


async def main():
    await run_chat(
        "general_return_policy",
        [{"role": "user", "content": "How many days do I have to return?"}],
    )
    await run_chat(
        "general_policies_overview",
        [{"role": "user", "content": "What are the policies of Vega?"}],
        expect_intent="general",
        expect_in_reply="policies",
    )
    await run_chat("general_bot", [{"role": "user", "content": "Are you a bot?"}])
    await run_chat("recommend", [{"role": "user", "content": "a birthday gift under $300"}])
    await run_chat(
        "recommend_concierge_chip_travel",
        [{"role": "user", "content": "Something compact for travel"}],
        expect_intent="recommend",
    )
    await run_chat(
        "recommend_concierge_chip_coffee",
        [{"role": "user", "content": "Gift for a coffee lover"}],
        expect_intent="recommend",
    )
    await run_chat(
        "recommend_concierge_chip_birthday",
        [{"role": "user", "content": "Birthday gift under $300"}],
        expect_intent="recommend",
    )
    await run_chat(
        [{"role": "user", "content": "compare NS-001 and NS-002"}],
    )
    await run_chat(
        "search",
        [{"role": "user", "content": "search for wireless headphones"}],
    )
    await run_chat(
        "gift",
        [{"role": "user", "content": "write a gift message for my sister's birthday"}],
        expect_intent="gift",
    )
    await run_chat(
        "product_qa",
        [{"role": "user", "content": "Does it have noise cancellation?"}],
        context={"sku": "NS-001"},
    )
    await run_chat(
        "product_qa_tell_me_about",
        [{"role": "user", "content": "Tell me about Aura Bluetooth Headphones"}],
        context={"sku": "NS-001"},
        expect_intent="product_qa",
    )

    # F-053: stats Q&A (guest — catalog + sales)
    await run_chat(
        "stats_catalog_pt",
        [{"role": "user", "content": "Qual o produto mais caro e o mais barato?"}],
        expect_intent="stats",
        expect_in_reply="599",
    )
    await run_chat(
        "stats_bestseller_en",
        [{"role": "user", "content": "What is the best-selling product?"}],
        expect_intent="stats",
    )

    demo_id = _demo_user_id()
    if demo_id:
        from app.ai_features import account_stats, _digits_from_usd
        acct = account_stats(demo_id) or {}
        spend_digits = _digits_from_usd(acct["spend"]) if acct.get("spend") is not None else "1275"
        spend_key = spend_digits[-3:] if len(spend_digits) >= 3 else spend_digits
        await run_chat(
            "stats_account_spent_pt",
            [{"role": "user", "content": "Quanto de dinheiro eu já gastei com compras?"}],
            user_id=demo_id,
            expect_intent="stats",
            expect_in_reply=spend_key,
        )
        await run_chat(
            "stats_account_orders_pt",
            [{"role": "user", "content": "Quantas compras eu já fiz?"}],
            user_id=demo_id,
            expect_intent="stats",
            expect_in_reply=str(acct.get("orders", 3)),
        )
        FLAGS.price_hallucination = True
        await run_chat(
            "stats_hallucination",
            [{"role": "user", "content": "Qual o produto mais caro?"}],
            expect_intent="stats",
        )
        FLAGS.price_hallucination = False
    else:
        print("\n===== CHAT: stats account (skipped — no demo user) =====", file=sys.stderr)

    await run_chat(
        "stats_account_guest",
        [{"role": "user", "content": "Quanto eu já gastei?"}],
        expect_intent="stats",
        expect_in_reply="sign in",
    )

    order_id = _demo_delivered_order_id()
    if order_id:
        await run_chat(
            "returns",
            [{"role": "user", "content": "I want a refund for my order"}],
            context={"order_id": order_id},
        )
        FLAGS.refund_false_denial = True
        await run_chat(
            "returns_denial",
            [{"role": "user", "content": "please process my refund"}],
            context={"order_id": order_id},
        )
        FLAGS.refund_false_denial = False
    else:
        print("\n===== CHAT: returns (skipped — no DELIVERED demo order) =====", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
