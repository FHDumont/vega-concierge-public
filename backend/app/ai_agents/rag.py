"""Local retrieval services owned by the isolated AI-agent package.

The public helpers retain the former RAG contract and observable retriever names,
without a dependency on the retired ``app.features`` package.
"""
from __future__ import annotations

import csv
import math
import re
import threading
from pathlib import Path
from uuid import uuid4

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import PrivateAttr

from ..galileo_span import RETRIEVE_CATALOG_RUN_NAME, RETRIEVE_STORE_POLICIES_RUN_NAME
from ..settings import settings

POLICIES_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"
PRODUCTS_QA_CSV = Path(__file__).resolve().parents[2] / "data" / "catalog" / "products_qa.csv"
COLLECTION_POLICIES = "vega_policies"
COLLECTION_CATALOG = "vega_catalog"
DEFAULT_K = settings.rag_top_k

_SYNONYMS = {
    "devolucao": "return", "devolução": "return", "devolver": "return", "devolucoes": "return",
    "reembolso": "refund", "reembolsar": "refund", "estorno": "refund",
    "refunding": "refund", "returning": "return",
    "prazo": "days", "dias": "days", "janela": "window", "frete": "shipping",
    "entrega": "delivery", "entregar": "delivery", "gratis": "free", "grátis": "free",
    "garantia": "warranty", "defeito": "defect", "defeituoso": "defect",
    "pagamento": "payment", "pagar": "payment", "cartao": "card", "cartão": "card",
    "preco": "price", "preço": "price", "custo": "price", "pedido": "order",
    "produto": "product", "estoque": "stock",
}
_STOPWORDS = {
    "the", "and", "for", "you", "are", "our", "with", "that", "this", "não", "nao", "que",
    "para", "com", "uma", "dos", "das", "meu", "minha", "como", "qual", "quais", "quantos",
    "quanto", "posso", "tenho", "quero", "preciso", "onde", "quando", "sobre", "does", "can",
    "how", "what", "many", "much", "have", "long", "when", "where", "which", "would", "should",
}


def _tokens(text: str) -> list[str]:
    return [
        normalized
        for token in re.findall(r"[\wáàâãéêíóôõúüç]+", (text or "").lower())
        if len(normalized := _SYNONYMS.get(token, token)) > 2 and normalized not in _STOPWORDS
    ]


def _read_policy_markdown(path: Path) -> str:
    from ..store.tools import REFUND_WINDOW_DAYS

    return path.read_text(encoding="utf-8").replace("{{REFUND_WINDOW_DAYS}}", str(REFUND_WINDOW_DAYS))


def _split_sections(text: str, source: str) -> list[Document]:
    title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), source)
    sections = re.split(r"(?m)^## ", text)
    documents: list[Document] = []
    for section in sections[1:]:
        heading, _, body = section.partition("\n")
        if body.strip():
            documents.append(Document(
                page_content=f"{title} — {heading.strip()}\n{body.strip()}",
                metadata={"source": source, "section": heading.strip(), "title": title},
            ))
    return documents


def load_policy_files() -> list[dict]:
    if not POLICIES_DIR.is_dir():
        return []
    out = []
    for path in sorted(POLICIES_DIR.glob("*.md")):
        markdown = _read_policy_markdown(path)
        title = next((line[2:].strip() for line in markdown.splitlines() if line.startswith("# ")), path.stem)
        out.append({"slug": path.stem, "title": title, "markdown": markdown})
    return out


def load_products_qa() -> list[dict]:
    if not PRODUCTS_QA_CSV.is_file():
        return []
    with PRODUCTS_QA_CSV.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def policy_documents() -> list[Document]:
    return [
        document
        for path in sorted(POLICIES_DIR.glob("*.md")) if POLICIES_DIR.is_dir()
        for document in _split_sections(_read_policy_markdown(path), path.stem)
    ]


_TOPIC_POLICY_SLUG = {
    "return": "returns",
    "ship": "shipping",
    "warrant": "warranty",
    "pay": "payment",
}


def policy_topic_chunks(topic_key: str) -> list[dict]:
    """All sections from one policy file — structured layout for topic FAQ (e.g. refunds)."""
    slug = _TOPIC_POLICY_SLUG.get(topic_key)
    if not slug or not POLICIES_DIR.is_dir():
        return []
    path = POLICIES_DIR / f"{slug}.md"
    if not path.is_file():
        return []
    markdown = _read_policy_markdown(path)
    return documents_to_policy_chunks(_split_sections(markdown, slug))


def policy_overview_chunks() -> list[dict]:
    """One representative chunk per policy file — deterministic overview for store chat."""
    skip_sections = {"using the store", "overview", "introduction", "about", "contact"}
    chunks: list[dict] = []
    if not POLICIES_DIR.is_dir():
        return chunks
    for path in sorted(POLICIES_DIR.glob("*.md")):
        markdown = _read_policy_markdown(path)
        sections = _split_sections(markdown, path.stem)
        if sections:
            filtered = [
                doc for doc in sections
                if doc.metadata.get("section", "").lower() not in skip_sections
            ]
            pool = filtered or sections
            pick = max(pool, key=lambda doc: len(doc.page_content or ""))
            chunks.extend(documents_to_policy_chunks([pick]))
        else:
            title = path.stem.replace("_", " ").title()
            chunks.append({
                "source": path.stem,
                "section": title,
                "text": markdown.strip()[:400],
            })
    return chunks


def catalog_documents() -> list[Document]:
    from ..store.tools import CATALOG

    qa_by_sku = {row["sku"]: row for row in load_products_qa()}
    documents = []
    for product in CATALOG:
        documents.append(Document(
            page_content=(
                f"{product['name']} (SKU {product['sku']})\n{product['description']}\n"
                f"Tags: {', '.join(product['tags'])}. Price: ${product['price']:.2f}."
            ),
            metadata={"source": "catalog", "sku": product["sku"], "name": product["name"], "price": product["price"]},
        ))
        if qa := qa_by_sku.get(product["sku"]):
            documents.append(Document(
                page_content=f"[Product FAQ] {qa['question']}\n{qa['answer']}",
                metadata={"source": "catalog_faq", "sku": product["sku"], "question": qa["question"], "answer": qa["answer"]},
            ))
    return documents


_CORPORA = {COLLECTION_POLICIES: policy_documents, COLLECTION_CATALOG: catalog_documents}


class VegaKeywordRetriever(BaseRetriever):
    """In-process keyword retriever that emits LangChain retriever spans."""

    documents: list[Document]
    k: int = DEFAULT_K
    _heads: list[set[str]] = PrivateAttr(default_factory=list)
    _bodies: list[set[str]] = PrivateAttr(default_factory=list)
    _idf: dict[str, float] = PrivateAttr(default_factory=dict)

    def model_post_init(self, _context) -> None:
        for document in self.documents:
            self._bodies.append(set(_tokens(document.page_content)))
            self._heads.append(set(_tokens(
                f"{document.metadata.get('title', '')} {document.metadata.get('section', '')}"
            )))
        total = len(self.documents) or 1
        frequencies: dict[str, int] = {}
        for body in self._bodies:
            for token in body:
                frequencies[token] = frequencies.get(token, 0) + 1
        self._idf = {token: math.log(1 + total / count) for token, count in frequencies.items()}

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return self.documents[:self.k]
        scores = []
        for index, body in enumerate(self._bodies):
            score = sum(
                self._idf.get(token, math.log(1 + len(self.documents)))
                * ((2.0 if token in self._heads[index] else 0.0) + (1.0 if token in body else 0.0))
                for token in query_tokens
            )
            if score > 0:
                scores.append((score, index))
        return [self.documents[index] for _, index in sorted(scores, key=lambda item: (-item[0], item[1]))[:self.k]] or self.documents[:self.k]


_retrievers: dict[str, BaseRetriever] = {}
_lock = threading.Lock()


def is_pgvector_enabled() -> bool:
    return settings.rag_enabled and bool(settings.rag_database_url)


def database_url() -> str:
    return settings.rag_database_url


def embedding_provider() -> str:
    return settings.rag_embedding_provider.strip().lower()


def embeddings():
    if embedding_provider() == "ollama":
        from langchain_ollama import OllamaEmbeddings
        return OllamaEmbeddings(base_url=settings.ollama_base_url, model=settings.rag_embedding_model or "nomic-embed-text")
    if embedding_provider() == "openai":
        from langchain_openai import OpenAIEmbeddings
        from ..llm.http_ssl import sync_http_client
        return OpenAIEmbeddings(model=settings.rag_embedding_model or "text-embedding-3-small", http_client=sync_http_client(60.0))
    raise ValueError("RAG_EMBEDDING_PROVIDER must be 'ollama' or 'openai'.")


_engine = None


def _pg_engine():
    global _engine
    if _engine is None:
        from sqlalchemy import create_engine
        _engine = create_engine(database_url(), pool_pre_ping=True, pool_recycle=300)
    return _engine


def vector_store(collection: str):
    from langchain_postgres import PGVector
    return PGVector(embeddings=embeddings(), collection_name=collection, connection=_pg_engine(), use_jsonb=True)


def _build_retriever(collection: str, k: int) -> BaseRetriever:
    if is_pgvector_enabled():
        try:
            return vector_store(collection).as_retriever(search_kwargs={"k": k})
        except Exception:  # noqa: BLE001 - the local keyword fallback keeps the app available.
            pass
    return VegaKeywordRetriever(documents=_CORPORA[collection](), k=k)


def get_retriever(collection: str, *, k: int = DEFAULT_K) -> BaseRetriever:
    if collection not in _CORPORA:
        raise ValueError(f"Unknown collection {collection!r}.")
    key = f"{collection}:{k}"
    with _lock:
        return _retrievers.setdefault(key, _build_retriever(collection, k))


def reset() -> None:
    with _lock:
        _retrievers.clear()


def backend_of(collection: str, *, k: int = DEFAULT_K) -> str:
    return "keyword" if isinstance(get_retriever(collection, k=k), VegaKeywordRetriever) else "pgvector"


def backend_name() -> str:
    return backend_of(COLLECTION_POLICIES)


def retrieve(collection: str, query: str, *, k: int = DEFAULT_K, config=None) -> list[Document]:
    try:
        return get_retriever(collection, k=k).invoke(query, config=config)
    except Exception:
        if backend_of(collection, k=k) == "keyword":
            raise
        with _lock:
            _retrievers[f"{collection}:{k}"] = VegaKeywordRetriever(documents=_CORPORA[collection](), k=k)
        return get_retriever(collection, k=k).invoke(query, config=config)


def _retriever_runnable(collection: str, label: str, k: int):
    return get_retriever(collection, k=k).with_config({"run_name": label, "name": label})


def policy_retriever_runnable(*, k: int = DEFAULT_K):
    return _retriever_runnable(COLLECTION_POLICIES, RETRIEVE_STORE_POLICIES_RUN_NAME, k)


def catalog_retriever_runnable(*, k: int = 20):
    return _retriever_runnable(COLLECTION_CATALOG, RETRIEVE_CATALOG_RUN_NAME, k)


def documents_to_policy_chunks(documents: list[Document]) -> list[dict]:
    return [{"source": item.metadata.get("source", ""), "section": item.metadata.get("section", ""), "text": item.page_content} for item in documents]


def format_catalog_documents(documents: list[Document]) -> str:
    return "Catalog excerpts:\n" + "\n\n".join(item.page_content for item in documents) + "\n\n" if documents else ""


def format_policy_documents(documents: list[Document]) -> str:
    return "Store policy excerpts:\n" + "\n\n".join(item.page_content for item in documents) + "\n\n" if documents else ""


def retrieve_policies(query: str, *, k: int = DEFAULT_K, config=None) -> list[dict]:
    return documents_to_policy_chunks(policy_retriever_runnable(k=k).invoke(query, config=config))


def policy_chunks_offline(query: str, *, k: int = DEFAULT_K) -> list[dict]:
    manager = CallbackManagerForRetrieverRun(
        run_id=uuid4(), handlers=[], inheritable_handlers=[], parent_run_id=None, tags=[], metadata={},
    )
    return documents_to_policy_chunks(
        VegaKeywordRetriever(documents=policy_documents(), k=k)._get_relevant_documents(query, run_manager=manager)
    )


def rank_skus(query: str, *, k: int = 20, config=None) -> list[str]:
    return [sku for document in catalog_retriever_runnable(k=k).invoke(query, config=config) if (sku := document.metadata.get("sku"))]
