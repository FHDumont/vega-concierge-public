"""Instance health — version, RAG, Ollama on host, and cascade provider count."""
from fastapi import APIRouter
from ..ai_agents import rag
from ..llm import llm_config
from ..settings import settings

# No `prefix`: each route carries the full path, just like it was in `api.py`.
router = APIRouter()


def _ollama_health() -> dict:
    """Probe Ollama /api/tags — host-side LLM/embeddings (F-REAL-ENV-1)."""
    import json
    import urllib.error
    import urllib.request

    base = settings.ollama_base_url.rstrip("/")
    embed_model = settings.rag_embedding_model or "nomic-embed-text"
    chat_model = settings.ollama_chat_model
    out = {
        "base_url": base,
        "reachable": False,
        "embed_model": embed_model,
        "chat_model": chat_model,
        "embed_model_present": False,
        "chat_model_present": False,
    }
    try:
        req = urllib.request.Request(f"{base}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            payload = json.loads(resp.read().decode())
        names = {m.get("name", "").split(":")[0] for m in payload.get("models", [])}
        out["reachable"] = True
        embed_base = embed_model.split(":")[0]
        chat_base = chat_model.split(":")[0]
        out["embed_model_present"] = embed_base in names
        out["chat_model_present"] = chat_base in names
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
        pass
    return out


@router.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": settings.vega_version,
        "git_sha": settings.vega_git_sha,
        "build_date": settings.vega_build_date or None,
        "environment": settings.deployment_environment,
        "rag": {
            "enabled": rag.is_pgvector_enabled(),
            "backend": rag.backend_name(),
            "embedding_provider": rag.embedding_provider(),
        },
        "ollama": _ollama_health(),
        "llm_providers": len(llm_config.list_providers()),
    }
