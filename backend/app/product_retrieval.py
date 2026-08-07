"""Product Q&A retrieval facade — keeps UC-1 isolated from other ai_agents modules."""
from __future__ import annotations

from app.ai_agents import rag


def retrieve_catalog_excerpts(product: dict, question: str, *, config=None) -> str:
    """Retrieve catalog chunks and format them for the product QA prompt."""
    query = f"{product['name']} {product['sku']} {question}"
    catalog_docs = rag.catalog_retriever_runnable(k=3).invoke(query, config=config)
    return rag.format_catalog_documents(catalog_docs) if catalog_docs else ""
