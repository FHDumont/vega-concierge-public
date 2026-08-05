"""Structured chat answer layouts — readable sections/facts without markdown (F-SMOKE-STAB-1)."""
from __future__ import annotations

import re
from typing import Any

_POLICY_LABELS = {
    "returns": "Returns & refunds",
    "shipping": "Shipping & delivery",
    "warranty": "Warranty",
    "payment": "Payment",
}

_TOPIC_TITLES = {
    "return": "Returns & refunds",
    "ship": "Shipping & delivery",
    "warrant": "Warranty",
    "pay": "Payment",
}

_TOPIC_WORDS = (
    ("return", ("return", "refund")),
    ("ship", ("ship", "delivery", "deliver")),
    ("warrant", ("warrant", "warranty")),
    ("pay", ("pay", "payment", "card", "pix")),
)


def _first_sentence(text: str, *, max_len: int = 160) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", raw, maxsplit=1)
    lead = parts[0].strip()
    if len(lead) > max_len:
        return lead[: max_len - 1].rstrip() + "…"
    return lead


def _split_sentences(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    parts = re.split(r"(?<=[.!?])\s+", raw)
    return [p.strip() for p in parts if p.strip()]


def _chunk_topic(chunk: dict) -> str:
    source = (chunk.get("source") or "").replace(".md", "").lower()
    for key, label in _POLICY_LABELS.items():
        if key in source:
            return label
    clean = source.replace("_", " ").strip()
    return clean.title() if clean else "Policy"


def _first_policy_paragraph(text: str, *, max_len: int = 220) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    for para in raw.split("\n\n"):
        clean = para.strip().lstrip("#").strip()
        clean = re.sub(r"\*\*", "", clean)
        if len(clean) < 20 or clean.startswith("#"):
            continue
        if len(clean) > max_len:
            return clean[: max_len - 1].rstrip() + "…"
        return clean
    return ""


def policy_sections_from_chunks(
    chunks: list[dict],
    *,
    body_max_len: int = 220,
) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    seen: set[str] = set()
    for chunk in chunks or []:
        title = _chunk_topic(chunk)
        if title in seen:
            continue
        body = _first_policy_paragraph(chunk.get("text") or "", max_len=body_max_len)
        if not body:
            continue
        sections.append({"title": title, "body": body})
        seen.add(title)
    return sections


def _topic_for_question(question: str) -> str | None:
    q = (question or "").lower()
    for key, words in _TOPIC_WORDS:
        if any(w in q for w in words):
            return key
    return None


def _sections_from_answer(answer: str) -> list[dict[str, str]]:
    sentences = _split_sentences(answer)
    if len(sentences) < 2:
        return []
    buckets: dict[str, list[str]] = {key: [] for key, _ in _TOPIC_WORDS}
    other: list[str] = []
    for sentence in sentences:
        low = sentence.lower()
        placed = False
        for key, words in _TOPIC_WORDS:
            if any(w in low for w in words):
                buckets[key].append(sentence)
                placed = True
                break
        if not placed:
            other.append(sentence)
    sections: list[dict[str, str]] = []
    for key, sents in buckets.items():
        if not sents:
            continue
        sections.append({"title": _TOPIC_TITLES[key], "body": " ".join(sents)})
    if other and len(sections) >= 1:
        sections.append({"title": "Also good to know", "body": " ".join(other)})
    return sections if len(sections) >= 2 else []


def _line_to_fact(line: str) -> dict[str, str] | None:
    if ": " not in line:
        return None
    label, value = line.split(": ", 1)
    label = label.strip()
    value = value.strip()
    if not label or not value:
        return None
    return {"label": label, "value": value}


def build_store_chat_layout(
    question: str,
    answer: str,
    chunks: list[dict] | None,
    *,
    overview: bool = False,
) -> dict[str, Any] | None:
    """Policy/general answers — sections when multi-topic or overview."""
    answer = (answer or "").strip()
    if not answer:
        return None

    sections = (
        policy_sections_from_chunks(chunks or [], body_max_len=320 if overview else 220)
        if chunks else []
    )
    if not sections:
        sections = _sections_from_answer(answer)

    topic = _topic_for_question(question)
    if not sections and topic:
        sections = [{"title": _TOPIC_TITLES[topic], "body": answer}]

    if not overview and topic:
        topic_title = _TOPIC_TITLES[topic]
        matching = [
            s for s in sections
            if topic_title.split("&")[0].strip().lower() in s["title"].lower()
            or topic in s["title"].lower()
        ]
        if matching:
            sections = matching[:1]
        else:
            sections = [{"title": topic_title, "body": answer}]

    if overview and sections:
        return {"lead": "Here's an overview of Vega's store policies:", "sections": sections[:6]}

    if len(sections) >= 2:
        lead = _first_sentence(answer) if len(answer) > 120 else sections[0]["body"][:120]
        return {"lead": lead, "sections": sections[:5]}

    if len(sections) == 1 and (len(answer) > 60 or topic):
        return {"lead": _first_sentence(answer), "sections": sections}

    bullets = _split_sentences(answer)
    if len(bullets) >= 3 and len(answer) > 120:
        return {"lead": bullets[0], "bullets": bullets[1:5]}

    if len(answer.split()) < 8 and not topic:
        return None

    return None


def build_stats_layout(facts: dict, scopes: set[str]) -> dict[str, Any] | None:
    """Stats answers — fact rows from authoritative context."""
    from .ai_features import (
        _account_stats_lines,
        _catalog_stats_lines,
        _sales_stats_lines,
        account_stats,
        catalog_stats,
        store_sales_stats,
    )

    rows: list[dict[str, str]] = []
    active = scopes or set()
    if "catalog" in active:
        cat = facts.get("catalog") or catalog_stats()
        for line in _catalog_stats_lines(cat):
            fact = _line_to_fact(line)
            if fact:
                rows.append(fact)
    if "sales" in active:
        sales = facts.get("sales") or store_sales_stats()
        for line in _sales_stats_lines(sales):
            fact = _line_to_fact(line)
            if fact:
                rows.append(fact)
    if "account" in active:
        acct = facts.get("account")
        if acct is None:
            rows.append({"label": "Account", "value": "Sign in to see your purchase history."})
        else:
            for line in _account_stats_lines(acct):
                fact = _line_to_fact(line)
                if fact:
                    rows.append(fact)

    if len(rows) < 2:
        return None
    return {"lead": "Here are the numbers:", "facts": rows[:8]}


_PRODUCT_OVERVIEW_RE = re.compile(
    r"\b(tell me about|what is|what's|how is|how's|describe|info on|information on|details on)\b",
    re.I,
)


def is_product_overview_question(question: str) -> bool:
    """Pergunta pedindo visão geral do produto (PDP / chat com SKU de contexto)."""
    return bool(_PRODUCT_OVERVIEW_RE.search(question or ""))


def _spec_bullets_from_qa(qa_row: dict | None) -> list[str]:
    """Extrai bullets legíveis do bloco `answer` do products_qa.csv."""
    if not qa_row or not qa_row.get("answer"):
        return []
    parts = re.split(r"\.\s+(?=[A-Z])", qa_row["answer"])
    return [p.strip().rstrip(".") for p in parts if p.strip() and ":" in p]


def build_product_qa_layout(
    product: dict, answer: str, *, question: str = "",
) -> dict[str, Any] | None:
    """Product Q&A — facts + spec bullets; sem duplicar lead truncado + seção Answer."""
    from . import rag
    from .ai_features import _availability, _usd

    answer = (answer or "").strip()
    if not answer:
        return None

    facts: list[dict[str, str]] = [{"label": "Product", "value": product["name"]}]
    low = answer.lower()
    if any(h in low for h in ("price", "cost", "$")):
        facts.append({"label": "Price", "value": _usd(product["price"])})
    if any(h in low for h in ("stock", "available", "availability")):
        facts.append({"label": "Availability", "value": _availability(product)})

    if is_product_overview_question(question):
        qa = next((row for row in rag.load_products_qa() if row["sku"] == product["sku"]), None)
        bullets = _spec_bullets_from_qa(qa)
        overview_facts = [
            {"label": "Product", "value": product["name"]},
            {"label": "Price", "value": _usd(product["price"])},
            {"label": "Availability", "value": _availability(product)},
        ]
        if bullets:
            return {"facts": overview_facts, "bullets": bullets[:6]}
        return {"facts": overview_facts}

    if len(answer) < 70 and len(facts) <= 1:
        return None

    sentences = _split_sentences(answer)
    if len(sentences) >= 2:
        return {"facts": facts[:4], "bullets": sentences[1:4]}
    if len(facts) >= 2:
        return {"facts": facts[:4]}
    return None


def build_compare_layout(verdict: str, product_a: dict, product_b: dict) -> dict[str, Any] | None:
    """Compare verdict — bullets when the model wrote multiple sentences."""
    from .ai_features import _usd

    verdict = (verdict or "").strip()
    if not verdict:
        return None
    sentences = _split_sentences(verdict)
    facts = [
        {"label": product_a["name"], "value": _usd(product_a["price"])},
        {"label": product_b["name"], "value": _usd(product_b["price"])},
    ]
    if len(sentences) >= 2:
        return {"lead": sentences[0], "facts": facts, "bullets": sentences[1:4]}
    if len(verdict) > 100:
        return {"lead": _first_sentence(verdict), "facts": facts, "sections": [{"title": "Verdict", "body": verdict}]}
    return {"facts": facts, "sections": [{"title": "Verdict", "body": verdict}]}
