"""Sentinelas do que os scripts de operação importam por NOME.

`scripts/lib/fresh-state.sh` e `scripts/lib/rag-init.sh` chamam o backend por linha de comando,
fora do import da app. Nenhum teste de API pega uma renomeação aqui — só este arquivo pega.
Se um destes falhar, o script de ops quebra em produção, não no CI.
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
    # scripts/lib/rag-init.sh:105/113 chamam `python setup_vectordb.py` pelo caminho.
    assert (BACKEND_ROOT / "setup_vectordb.py").is_file()


def test_llm_config_module_keeps_its_name():
    # O basename do módulo é parte do contrato com o fresh-state.sh (renomear é fase futura).
    import app.llm.llm_config as module

    assert module.__name__ == "app.llm.llm_config"


def _imported_modules(module_file: str) -> set[str]:
    """Módulos (caminho `app.`-qualificado completo) importados por um arquivo de `app/`, em
    QUALQUER nível (topo ou dentro de função) — é justamente o import adiado que costuma
    reintroduzir ciclo sem ninguém notar. Resolve `from ..features import store_qa` para
    `app.features.store_qa` em vez de só `features` — um subpacote (F-BACKEND-2) faria isso
    colapsar pro nome do subpacote e cegar o guard pra violação nenhuma."""
    import ast

    path = Path(module_file)
    pkg_parts = ("app", *path.parent.parts)  # ex.: "store/catalog_format.py" → ("app", "store")
    tree = ast.parse((BACKEND_ROOT / "app" / module_file).read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            base_parts = pkg_parts[: len(pkg_parts) - (node.level - 1)] if node.level > 1 else pkg_parts
            base = ".".join(base_parts) + (f".{node.module}" if node.module else "")
            out.update(f"{base}.{a.name}" for a in node.names)
    return out


def test_catalog_format_stays_pure_formatting():
    # Formatação não pode reintroduzir dependências de dados, IA ou LLM.
    imported = _imported_modules("store/catalog_format.py")
    forbidden_prefixes = {
        "app.ai_agents.",
        "app.store.tools", "app.store.orders", "app.llm.",
    }
    hits = {m for m in imported if any(m == p or m.startswith(p) for p in forbidden_prefixes)}
    assert not hits, imported
