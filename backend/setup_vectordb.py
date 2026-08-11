#!/usr/bin/env python3
"""Populates the pgvector collections (F-GALILEO-1 / F-RAG-LIVE, ADR-031). Idempotent.

With `RAG_ENABLED=1`, `./scripts/dev.sh` and `./scripts/up.sh` run this via `scripts/lib/rag-init.sh`.
Manual (host dev):

    cd backend
    pip install -r requirements-rag.txt
    export RAG_ENABLED=1 RAG_DATABASE_URL=postgresql+psycopg://vega:vega@localhost:5434/vega_rag
    export OPENAI_API_KEY=...
    python3 setup_vectordb.py
"""
import sys

from app.ai_agents import rag


def load(collection: str, documents: list) -> None:
    store = rag.vector_store(collection)
    # Rebuilds from scratch: the corpus is small and derived, so reindexing is cheaper (and more
    # honest) than trying to reconcile upserts by id.
    try:
        store.delete_collection()
    except Exception as exc:  # noqa: BLE001 — first run: the collection doesn't exist yet
        print(f"  (new collection: {type(exc).__name__})")
    store.create_collection()
    store.add_documents(documents)
    print(f"  {collection}: {len(documents)} chunks")


def main() -> int:
    if not rag.is_pgvector_enabled():
        print("RAG_ENABLED != 1 or RAG_DATABASE_URL empty — nothing to do.")
        print("Vega works without this: the keyword retriever is the default.")
        return 1
    print(f"Indexing at {rag.database_url().rsplit('@', 1)[-1]}")
    load(rag.COLLECTION_POLICIES, rag.policy_documents())
    load(rag.COLLECTION_CATALOG, rag.catalog_documents())
    rag.reset()
    print(f"Done. Active retriever: {rag.backend_name()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
