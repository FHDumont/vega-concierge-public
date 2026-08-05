#!/usr/bin/env python3
"""Popula as collections do pgvector (F-GALILEO-1 / F-RAG-LIVE, ADR-031). Idempotente.

Com `RAG_ENABLED=1`, `./scripts/dev.sh` e `./scripts/up.sh` rodam isto via `scripts/lib/rag-init.sh`.
Manual (host dev):

    cd backend
    pip install -r requirements-rag.txt
    export RAG_ENABLED=1 RAG_DATABASE_URL=postgresql+psycopg://vega:vega@localhost:5434/vega_rag
    export OPENAI_API_KEY=...
    python3 setup_vectordb.py
"""
import sys

from app import rag


def load(collection: str, documents: list) -> None:
    store = rag.vector_store(collection)
    # Recria do zero: o corpus é pequeno e derivado, então reindexar é mais barato (e mais
    # honesto) do que tentar casar upserts por id.
    try:
        store.delete_collection()
    except Exception as exc:  # noqa: BLE001 — primeira execução: a collection ainda não existe
        print(f"  (collection nova: {type(exc).__name__})")
    store.create_collection()
    store.add_documents(documents)
    print(f"  {collection}: {len(documents)} chunks")


def main() -> int:
    if not rag.is_pgvector_enabled():
        print("RAG_ENABLED != 1 ou RAG_DATABASE_URL vazio — nada a fazer.")
        print("O Vega funciona sem isto: o retriever keyword é o default.")
        return 1
    print(f"Indexando em {rag.database_url().rsplit('@', 1)[-1]}")
    load(rag.COLLECTION_POLICIES, rag.policy_documents())
    load(rag.COLLECTION_CATALOG, rag.catalog_documents())
    rag.reset()
    print(f"Pronto. Retriever ativo: {rag.backend_name()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
