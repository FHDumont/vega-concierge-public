"""Store policy Q&A for shopper chat (`feature.answer_store_policy`)."""
from __future__ import annotations

import time

from ..llm.agent_llm_invoke import invoke_feature_llm, is_stub_output
from ..chat_layout import _topic_for_question, build_store_chat_layout, shopper_reply_from_layout
from ..store.langchain_tools import search_policies_tool
from ..problems import FLAGS
from . import rag

LLM_RUN_NAME = "feature.answer_store_policy"
_SYSTEM_PROMPT = (
    "You are Vega's concise shopper concierge. Answer using ONLY the policy facts supplied. "
    "Be concise. Reply in English without markdown."
)
_POLICY_OVERVIEW_HINTS = (
    "policies", "policy", "what are your", "what is your", "store policies", "your policies",
)
_POLICY_TOPIC_MARKERS = (
    ("return", ("return", "refund")),
    ("ship", ("ship", "delivery", "deliver")),
    ("warrant", ("warrant", "warranty")),
    ("pay", ("pay", "payment", "card", "pix")),
)


def _is_policy_overview_question(question: str) -> bool:
    q = (question or "").lower()
    if not any(h in q for h in _POLICY_OVERVIEW_HINTS):
        return False
    return not any(
        w in q for w in ("return", "refund", "ship", "delivery", "warrant", "pay", "payment", "bot")
    )


def _policy_overview_from_chunks(chunks: list[dict]) -> str:
    from ..chat_layout import policy_sections_from_chunks

    sections = policy_sections_from_chunks(chunks, body_max_len=320)
    if sections:
        bodies = [f"{section['title']}: {section['body']}" for section in sections[:6]]
        return "Here's an overview of Vega's store policies:\n\n" + "\n\n".join(bodies)
    return (
        "Vega's store policies include a 30-day return window from delivery with full refunds, "
        "free standard shipping in Brazil within about 2 business days, a 12-month warranty on "
        "defects, and payment by credit card or Pix with no charge if an order fails."
    )


def _is_explanatory_policy_question(question: str) -> bool:
    q = (question or "").lower()
    return any(h in q for h in ("how", "works", "work", "explain", "tell me about"))


def _store_overview_answer_ok(answer: str) -> bool:
    low = (answer or "").lower()
    hits = sum(1 for _key, words in _POLICY_TOPIC_MARKERS if any(w in low for w in words))
    return hits >= 2 and len(low.split()) >= 18


def _format_chunks(chunks: list[dict]) -> str:
    if not chunks:
        return ""
    lines = []
    for chunk in chunks:
        source = chunk.get("source") or "policy"
        section = chunk.get("section") or ""
        text = (chunk.get("text") or "").strip()
        if text:
            lines.append(f"[{source} — {section}]\n{text}" if section else f"[{source}]\n{text}")
    return "Store policy excerpts:\n\n" + "\n\n".join(lines) if lines else ""


def _store_chat_fallback(question: str, chunks: list[dict], grounded: bool) -> str:
    if not grounded:
        return "You can return anything within 90 days for a full refund — no questions asked!"
    q = question.lower()
    if any(w in q for w in ("bot", "who are you", "are you")):
        return "I'm the Vega store chatbot — here to help with policies, products, and orders."
    for chunk in chunks:
        text = (chunk.get("text") or "").strip()
        if not text:
            continue
        if "30 days" in text.lower() and any(w in q for w in ("return", "refund", "days")):
            return "You have 30 days from the delivery date to request a return for any reason."
        if any(w in q for w in ("ship", "delivery")) and "ship" in text.lower():
            return text.split("\n\n")[0][:240]
        if any(w in q for w in ("warrant", "warranty")) and "warrant" in text.lower():
            return text.split("\n\n")[0][:240]
        if any(w in q for w in ("pay", "payment", "card")) and "pay" in text.lower():
            return text.split("\n\n")[0][:240]
    if chunks:
        first = (chunks[0].get("text") or "").strip()
        if first:
            return first.split("\n\n")[0][:240]
    if any(w in q for w in ("hi", "hello", "thanks")):
        return "Hello! How can I help you today?"
    return "I don't have that detail in our store policies — please contact support for more help."


def store_chat(question: str, *, config=None) -> dict:
    """Answer general store questions grounded in real policy RAG chunks."""
    question = (question or "").strip() or "Hello"
    grounded = not FLAGS.price_hallucination
    overview = _is_policy_overview_question(question)
    if FLAGS.latency_spike:
        time.sleep(1.2)

    chunks: list[dict] = []
    if grounded:
        retrieval = search_policies_tool.invoke({"question": question}, config=config)
        chunks = retrieval.get("chunks") or []
        context = _format_chunks(chunks)
        if overview:
            suffix = (
                "The customer wants a broad overview of Vega's store policies. Summarize in 5-7 "
                "sentences covering returns/refunds, shipping, warranty, payment, and privacy/terms "
                "using ONLY the policy excerpts above — include concrete numbers and timeframes when "
                "present. Reply in English. No markdown."
            )
            max_tokens = 320
        elif _is_explanatory_policy_question(question):
            suffix = (
                "Answer using ONLY the store policy excerpts above. Explain clearly in 3-5 "
                "sentences with concrete numbers and timeframes from the excerpts. Reply in English. "
                "No markdown."
            )
            max_tokens = 320
        else:
            suffix = (
                "Answer using ONLY the store policy excerpts above when relevant. For greetings or "
                "questions not covered by policy, reply helpfully as the store chatbot. If the "
                "question has multiple parts, answer every part the excerpts cover. Be concise "
                "(1-3 sentences). Reply in English. No markdown."
            )
            max_tokens = 180
        prompt = f"{context}\n\nShopper question: {question}\n\n{suffix}".strip()
        result = invoke_feature_llm(
            "store_chat", _SYSTEM_PROMPT, prompt,
            run_name=LLM_RUN_NAME, max_tokens=max_tokens, verbose=FLAGS.cost_spike, config=config,
        )
        text = result.text
    else:
        chunks = rag.policy_chunks_offline(question, k=2)
        prompt = (
            f"Shopper question: {question}\n\nYou have no store policy data. Answer confidently "
            "with specific figures anyway — never say you lack a detail. Reply in English. No markdown."
        )
        result = invoke_feature_llm(
            "store_chat", _SYSTEM_PROMPT, prompt,
            run_name=LLM_RUN_NAME, max_tokens=180, verbose=FLAGS.cost_spike, config=config,
        )
        text = result.text

    if is_stub_output(text) or result.system == "stub":
        topic = _topic_for_question(question)
        if overview and grounded:
            chunks_for_layout = rag.policy_overview_chunks()
        elif grounded and topic:
            chunks_for_layout = rag.policy_topic_chunks(topic) or chunks
        else:
            chunks_for_layout = chunks if grounded else []
        text = (
            _policy_overview_from_chunks(chunks_for_layout)
            if overview and chunks_for_layout
            else _store_chat_fallback(question, chunks_for_layout, grounded)
        )
    elif grounded and overview and not _store_overview_answer_ok(text):
        chunks_for_layout = rag.policy_overview_chunks() or chunks
        text = _policy_overview_from_chunks(chunks_for_layout)
    else:
        topic = _topic_for_question(question)
        if grounded and not overview and topic:
            chunks_for_layout = rag.policy_topic_chunks(topic) or chunks
        else:
            chunks_for_layout = rag.policy_overview_chunks() if overview and grounded else chunks

    layout = build_store_chat_layout(question, text, chunks_for_layout, overview=overview)
    answer = shopper_reply_from_layout(layout, text)
    return {
        "answer": answer.strip(),
        "grounded": grounded,
        "layout": layout,
    }
