"""Sentinels for what the ops scripts import by NAME.

`scripts/lib/fresh-state.sh` and `scripts/lib/rag-init.sh` call the backend from the command
line, outside of the app import. No API test catches a rename here — only this file does.
If one of these fails, the ops script breaks in production, not in CI.
"""
from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def test_fresh_state_can_export_the_provider_backup():
    # scripts/lib/fresh-state.sh:28
    #   python -c "from app.llm import llm_config; n=llm_config.export_providers_backup(); print(n)"
    from app.llm import llm_config

    assert callable(llm_config.export_providers_backup)
    assert callable(llm_config.restore_providers_backup)


def test_rag_init_can_read_the_backend_name():
    # scripts/lib/rag-init.sh:114
    #   python3 -c 'from app.ai_agents import rag; print(rag.backend_name())'
    from app.ai_agents import rag

    assert callable(rag.backend_name)
    assert isinstance(rag.backend_name(), str)


def test_setup_vectordb_stays_at_the_backend_root():
    # scripts/lib/rag-init.sh:105/113 call `python setup_vectordb.py` by path.
    assert (BACKEND_ROOT / "setup_vectordb.py").is_file()


def test_llm_config_module_keeps_its_name():
    # The module's basename is part of the contract with fresh-state.sh (renaming is a future phase).
    import app.llm.llm_config as module

    assert module.__name__ == "app.llm.llm_config"


def _imported_modules(module_file: str) -> set[str]:
    """Modules (full `app.`-qualified path) imported by an `app/` file, at
    ANY level (top-level or inside a function) — it's exactly the deferred import that tends to
    reintroduce a cycle without anyone noticing. Resolves `from ..features import store_qa` to
    `app.features.store_qa` instead of just `features` — a subpackage (F-BACKEND-2) would make this
    collapse to the subpackage name and blind the guard to any violation."""
    import ast

    path = Path(module_file)
    pkg_parts = ("app", *path.parent.parts)  # ex.: "store/catalog_format.py" -> ("app", "store")
    tree = ast.parse((BACKEND_ROOT / "app" / module_file).read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            base_parts = pkg_parts[: len(pkg_parts) - (node.level - 1)] if node.level > 1 else pkg_parts
            base = ".".join(base_parts) + (f".{node.module}" if node.module else "")
            out.update(f"{base}.{a.name}" for a in node.names)
    return out


def test_catalog_format_stays_pure_formatting():
    # Formatting must not reintroduce data, AI, or LLM dependencies.
    imported = _imported_modules("store/catalog_format.py")
    forbidden_prefixes = {
        "app.ai_agents.",
        "app.store.tools", "app.store.orders", "app.llm.",
    }
    hits = {m for m in imported if any(m == p or m.startswith(p) for p in forbidden_prefixes)}
    assert not hits, imported
