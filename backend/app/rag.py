"""RAG do Vega (F-GALILEO-1, ADR-031) — retrieval de políticas da loja e do catálogo.

Existe por um motivo de observabilidade, não de qualidade de busca: as métricas de RAG do
Console Splunk Agent Observability (Chunk Relevance, Chunk Attribution Utilization, Context Precision) só existem
sobre **retriever span**, e retriever span só nasce quando um `BaseRetriever` do LangChain roda
dentro do run traçado. `StructuredTool` sobre função pura gera *tool* span, que é outro nó.

Dois retrievers, os dois `BaseRetriever` (logo os dois emitem retriever span):

- `VegaKeywordRetriever` — scoring por token, em processo, sem infra. É o DEFAULT (standalone-first).
- pgvector — embeddings reais, opt-in por `RAG_ENABLED=1` + `RAG_DATABASE_URL` (profile `rag` do
  compose). Falha de conexão degrada p/ o keyword em vez de quebrar a request.

Dois corpora: `policies` (markdown em `backend/data/policies/`) e `catalog` (derivado de
`tools.CATALOG` — sem duplicar dado).
"""
from __future__ import annotations

import logging
import math
import os
import re
import threading
import csv
from pathlib import Path
from uuid import uuid4

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import PrivateAttr

from .galileo_span import RETRIEVE_CATALOG_RUN_NAME, RETRIEVE_STORE_POLICIES_RUN_NAME

log = logging.getLogger(__name__)

POLICIES_DIR = Path(__file__).resolve().parent.parent / "data" / "policies"
PRODUCTS_QA_CSV = Path(__file__).resolve().parent.parent / "data" / "catalog" / "products_qa.csv"

COLLECTION_POLICIES = "vega_policies"
COLLECTION_CATALOG = "vega_catalog"

DEFAULT_K = int(os.getenv("RAG_TOP_K", "3"))

# Termos PT → EN. O conteúdo de loja é em inglês (CONVENCOES), mas o shopper escreve nos dois
# idiomas (o concierge detecta pt/en desde a F-025) — sem isso o keyword retriever não acha nada
# numa pergunta em português. Só os termos de domínio; não é um tradutor.
_SYNONYMS = {
    "devolucao": "return", "devolução": "return", "devolver": "return", "devolucoes": "return",
    "reembolso": "refund", "reembolsar": "refund", "estorno": "refund",
    "prazo": "days", "dias": "days", "janela": "window",
    "frete": "shipping", "entrega": "delivery", "entregar": "delivery",
    "gratis": "free", "grátis": "free", "gratuito": "free",
    "garantia": "warranty", "defeito": "defect", "defeituoso": "defect",
    "pagamento": "payment", "pagar": "payment", "cartao": "card", "cartão": "card",
    "cobranca": "charge", "cobrança": "charge", "parcelas": "installments",
    "preco": "price", "preço": "price", "custo": "price",
    "pedido": "order", "produto": "product", "estoque": "stock",
}

_STOPWORDS = {
    "the", "and", "for", "you", "are", "our", "with", "that", "this", "não", "nao", "que",
    "para", "com", "uma", "dos", "das", "meu", "minha", "como", "qual", "quais", "quantos",
    "quanto", "posso", "tenho", "quero", "preciso", "onde", "quando", "sobre", "does", "can",
    "how", "what", "many", "much", "have", "long", "when", "where", "which", "would", "should",
}


def _tokens(text: str) -> list[str]:
    """Tokens normalizados p/ scoring: minúsculas, >2 chars, sem stopword, PT→EN aplicado."""
    raw = re.findall(r"[\wáàâãéêíóôõúüç]+", (text or "").lower())
    out = []
    for t in raw:
        t = _SYNONYMS.get(t, t)
        if len(t) > 2 and t not in _STOPWORDS:
            out.append(t)
    return out


# --- Corpora ----------------------------------------------------------------

def _split_sections(text: str, source: str) -> list[Document]:
    """Um chunk por seção `##` do markdown. O `#` do topo vira o título do documento."""
    lines = text.splitlines()
    doc_title = next((l[2:].strip() for l in lines if l.startswith("# ")), source)
    docs: list[Document] = []
    section, body = None, []

    def flush() -> None:
        if section and body:
            content = "\n".join(body).strip()
            if content:
                docs.append(Document(
                    page_content=f"{doc_title} — {section}\n{content}",
                    metadata={"source": source, "section": section, "title": doc_title},
                ))

    for line in lines:
        if line.startswith("## "):
            flush()
            section, body = line[3:].strip(), []
        elif section is not None:
            body.append(line)
    flush()
    return docs


def _read_policy_markdown(path: Path) -> str:
    """Lê markdown de política substituindo placeholders (DT-020: janela de devolução)."""
    from .tools import REFUND_WINDOW_DAYS

    return path.read_text(encoding="utf-8").replace(
        "{{REFUND_WINDOW_DAYS}}", str(REFUND_WINDOW_DAYS)
    )


def load_policy_files() -> list[dict]:
    """Políticas completas para API/UI — mesmos arquivos que `policy_documents()`, placeholders resolvidos."""
    policies: list[dict] = []
    if not POLICIES_DIR.is_dir():
        return policies
    for path in sorted(POLICIES_DIR.glob("*.md")):
        markdown = _read_policy_markdown(path)
        title = next(
            (line[2:].strip() for line in markdown.splitlines() if line.startswith("# ")),
            path.stem.replace("_", " ").title(),
        )
        policies.append({"slug": path.stem, "title": title, "markdown": markdown})
    return policies


def load_products_qa() -> list[dict]:
    """Specs estruturadas por SKU (`products_qa.csv`) — enriquece RAG e `_product_context`."""
    if not PRODUCTS_QA_CSV.is_file():
        return []
    with PRODUCTS_QA_CSV.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def policy_documents() -> list[Document]:
    """Chunks das políticas da loja (devolução, frete, garantia, pagamento)."""
    docs: list[Document] = []
    if not POLICIES_DIR.is_dir():
        return docs
    for path in sorted(POLICIES_DIR.glob("*.md")):
        docs.extend(_split_sections(_read_policy_markdown(path), path.stem))
    return docs


def catalog_documents() -> list[Document]:
    """Chunks de catálogo: marketing (`CATALOG`) + FAQ técnico por SKU (`products_qa.csv`)."""
    from .tools import CATALOG

    qa_by_sku = {row["sku"]: row for row in load_products_qa()}
    docs: list[Document] = []
    for p in CATALOG:
        docs.append(
            Document(
                page_content=(
                    f"{p['name']} (SKU {p['sku']})\n{p['description']}\n"
                    f"Tags: {', '.join(p['tags'])}. Price: ${p['price']:.2f}."
                ),
                metadata={"source": "catalog", "sku": p["sku"], "name": p["name"], "price": p["price"]},
            )
        )
        qa = qa_by_sku.get(p["sku"])
        if qa:
            docs.append(
                Document(
                    page_content=f"[Product FAQ] {qa['question']}\n{qa['answer']}",
                    metadata={
                        "source": "catalog_faq",
                        "sku": p["sku"],
                        "question": qa["question"],
                        "answer": qa["answer"],
                    },
                )
            )
    return docs


_CORPORA = {COLLECTION_POLICIES: policy_documents, COLLECTION_CATALOG: catalog_documents}


# --- Retriever keyword (default, sem infra) ---------------------------------

class VegaKeywordRetriever(BaseRetriever):
    """Retriever em processo: score = tokens da query presentes no chunk, ponderado por raridade
    do termo (IDF) e com peso extra em título/seção. A ponderação importa: sem ela uma palavra
    genérica no cabeçalho ("how long…") vence o termo do domínio ("warranty").

    É `BaseRetriever` de propósito — é isso que faz nascer o retriever span (ADR-031)."""

    documents: list[Document]
    k: int = DEFAULT_K
    _heads: list[set[str]] = PrivateAttr(default_factory=list)
    _bodies: list[set[str]] = PrivateAttr(default_factory=list)
    _idf: dict[str, float] = PrivateAttr(default_factory=dict)

    def model_post_init(self, _context) -> None:
        for doc in self.documents:
            self._bodies.append(set(_tokens(doc.page_content)))
            self._heads.append(set(_tokens(
                f"{doc.metadata.get('title', '')} {doc.metadata.get('section', '')}"
            )))
        total = len(self.documents) or 1
        df: dict[str, int] = {}
        for body in self._bodies:
            for token in body:
                df[token] = df.get(token, 0) + 1
        self._idf = {t: math.log(1 + total / n) for t, n in df.items()}

    def _score(self, query_tokens: list[str], i: int) -> float:
        head, body = self._heads[i], self._bodies[i]
        return sum(
            self._idf.get(t, math.log(1 + len(self.documents)))
            * ((2.0 if t in head else 0.0) + (1.0 if t in body else 0.0))
            for t in query_tokens
        )

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return self.documents[: self.k]
        scored = [(self._score(query_tokens, i), i) for i in range(len(self.documents))]
        hits = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))
        # Sem hit léxico devolvemos os primeiros chunks em vez de nada: o agente ainda recebe
        # contexto (e o Console mostra Chunk Relevance baixo, que é a informação útil).
        return [self.documents[i] for _, i in hits[: self.k]] or self.documents[: self.k]


# --- Retriever pgvector (opt-in) --------------------------------------------

def is_pgvector_enabled() -> bool:
    return os.getenv("RAG_ENABLED", "0") == "1" and bool(os.getenv("RAG_DATABASE_URL", ""))


def database_url() -> str:
    return os.getenv("RAG_DATABASE_URL", "")


def embedding_provider() -> str:
    return os.getenv("RAG_EMBEDDING_PROVIDER", "ollama").strip().lower()


def embeddings():
    """Modelo de embedding p/ o pgvector. Só chamado no caminho opt-in."""
    provider = embedding_provider()
    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        base_url = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
        model = os.getenv("RAG_EMBEDDING_MODEL", "nomic-embed-text")
        return OllamaEmbeddings(base_url=base_url, model=model)
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        from .http_ssl import sync_http_client

        return OpenAIEmbeddings(
            model=os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small"),
            http_client=sync_http_client(60.0),
        )
    raise ValueError(
        f"RAG_EMBEDDING_PROVIDER={provider!r} desconhecido — use 'ollama' (default) ou 'openai'."
    )


def vector_store(collection: str):
    """`PGVector` da collection. Levanta se as deps de `requirements-rag.txt` não estão instaladas."""
    from langchain_postgres import PGVector

    return PGVector(
        embeddings=embeddings(),
        collection_name=collection,
        connection=database_url(),
        use_jsonb=True,
    )


# --- Resolução + cache ------------------------------------------------------

_retrievers: dict[str, BaseRetriever] = {}
_lock = threading.Lock()


def _build_retriever(collection: str, k: int) -> BaseRetriever:
    if is_pgvector_enabled():
        try:
            return vector_store(collection).as_retriever(search_kwargs={"k": k})
        except Exception:  # noqa: BLE001 — degradação deliberada (ADR-031): keyword ainda serve
            pass
    return VegaKeywordRetriever(documents=_CORPORA[collection](), k=k)


def get_retriever(collection: str, *, k: int = DEFAULT_K) -> BaseRetriever:
    """Retriever da collection (pgvector se ligado e saudável, senão keyword). Cacheado."""
    if collection not in _CORPORA:
        raise ValueError(f"Unknown collection {collection!r}. Expected one of: {', '.join(_CORPORA)}")
    key = f"{collection}:{k}"
    with _lock:
        retriever = _retrievers.get(key)
        if retriever is None:
            retriever = _retrievers[key] = _build_retriever(collection, k)
        return retriever


def reset() -> None:
    """Descarta os retrievers cacheados (teste / reload de corpus)."""
    with _lock:
        _retrievers.clear()


def backend_of(collection: str, *, k: int = DEFAULT_K) -> str:
    """`pgvector` | `keyword` — o que está efetivamente servindo a collection (após degradação)."""
    return "keyword" if isinstance(get_retriever(collection, k=k), VegaKeywordRetriever) else "pgvector"


def backend_name() -> str:
    return backend_of(COLLECTION_POLICIES)


# --- API de uso -------------------------------------------------------------

def retrieve(collection: str, query: str, *, k: int = DEFAULT_K, config=None) -> list[Document]:
    """Invoca o retriever passando `config` — é o `config` que carrega os callbacks e, portanto,
    o que faz o retriever span aparecer no trace.

    Postgres pode cair DEPOIS do retriever entrar no cache, e aí a falha é em tempo de consulta,
    não de construção. Nesse caso trocamos a collection pelo keyword e repetimos (ADR-031: o RAG
    degrada, não derruba a request)."""
    try:
        return get_retriever(collection, k=k).invoke(query, config=config)
    except Exception:  # noqa: BLE001 — degradação deliberada (ADR-031)
        if backend_of(collection, k=k) == "keyword":
            raise
        log.warning("rag: retriever pgvector falhou em consulta; caindo para keyword", exc_info=True)
        with _lock:
            _retrievers[f"{collection}:{k}"] = VegaKeywordRetriever(
                documents=_CORPORA[collection](), k=k
            )
    return get_retriever(collection, k=k).invoke(query, config=config)


def policy_retriever_runnable(*, k: int = DEFAULT_K) -> BaseRetriever:
    """Retriever de políticas com `run_name`/`name` legíveis — aninha como L4r sob a feature chain."""
    label = RETRIEVE_STORE_POLICIES_RUN_NAME
    return get_retriever(COLLECTION_POLICIES, k=k).with_config({"run_name": label, "name": label})


def catalog_retriever_runnable(*, k: int = 20) -> BaseRetriever:
    """Retriever de catálogo com `run_name`/`name` legíveis — aninha como L4r sob a feature chain."""
    label = RETRIEVE_CATALOG_RUN_NAME
    return get_retriever(COLLECTION_CATALOG, k=k).with_config({"run_name": label, "name": label})


def documents_to_policy_chunks(docs: list[Document]) -> list[dict]:
    """Forma serializável dos chunks — mesma shape que `retrieve_policies`."""
    return [
        {
            "source": d.metadata.get("source", ""),
            "section": d.metadata.get("section", ""),
            "text": d.page_content,
        }
        for d in docs
    ]


def format_catalog_documents(docs: list[Document]) -> str:
    """Trechos do catálogo (marketing + FAQ por SKU) — p/ product_qa com retrieve_catalog."""
    if not docs:
        return ""
    return "Catalog excerpts:\n" + "\n\n".join(d.page_content for d in docs) + "\n\n"


def format_policy_documents(docs: list[Document]) -> str:
    """Trechos serializáveis das políticas — mesmo formato que `_policy_context` usava."""
    if not docs:
        return ""
    return "Store policy excerpts:\n" + "\n\n".join(d.page_content for d in docs) + "\n\n"


def retrieve_policies(query: str, *, k: int = DEFAULT_K, config=None) -> list[dict]:
    """Chunks de política relevantes → `[{source, section, text}]` (forma serializável p/ tool)."""
    docs = policy_retriever_runnable(k=k).invoke(query, config=config)
    return documents_to_policy_chunks(docs)


def policy_chunks_offline(query: str, *, k: int = DEFAULT_K) -> list[dict]:
    """Ranking keyword in-process sem `invoke` — fallback/stub pós-chain (sem retriever span órfão)."""
    retriever = VegaKeywordRetriever(documents=policy_documents(), k=k)
    run_manager = CallbackManagerForRetrieverRun(
        run_id=uuid4(),
        handlers=[],
        inheritable_handlers=[],
        parent_run_id=None,
        tags=[],
        metadata={},
        name=RETRIEVE_STORE_POLICIES_RUN_NAME,
    )
    docs = retriever._get_relevant_documents(query, run_manager=run_manager)
    return documents_to_policy_chunks(docs)


def rank_skus(query: str, *, k: int = 20, config=None) -> list[str]:
    """SKUs do catálogo ordenados por relevância à query (usado p/ ordenar `search_catalog`)."""
    docs = catalog_retriever_runnable(k=k).invoke(query, config=config)
    return [sku for d in docs if (sku := d.metadata.get("sku"))]
