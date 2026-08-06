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
    #   python -c "from app import llm_config; n=llm_config.export_providers_backup(); print(n)"
    from app import llm_config

    assert callable(llm_config.export_providers_backup)
    assert callable(llm_config.restore_providers_backup)


def test_rag_init_can_read_the_backend_name():
    # scripts/lib/rag-init.sh:114
    #   python3 -c 'from app import rag; print(rag.backend_name())'
    from app import rag

    assert callable(rag.backend_name)
    assert isinstance(rag.backend_name(), str)


def test_setup_vectordb_stays_at_the_backend_root():
    # scripts/lib/rag-init.sh:105/113 chamam `python setup_vectordb.py` pelo caminho.
    assert (BACKEND_ROOT / "setup_vectordb.py").is_file()


def test_llm_config_module_keeps_its_name():
    # O nome do módulo é parte do contrato com o fresh-state.sh (renomear é fase futura).
    import app.llm_config as module

    assert module.__name__ == "app.llm_config"


def _imported_modules(module_file: str) -> set[str]:
    """Módulos irmãos importados por um arquivo de `app/`, em QUALQUER nível (topo ou dentro de
    função) — é justamente o import adiado que costuma reintroduzir ciclo sem ninguém notar."""
    import ast

    tree = ast.parse((BACKEND_ROOT / "app" / module_file).read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            if node.module:
                out.add(node.module.split(".")[0])
            else:
                out.update(a.name.split(".")[0] for a in node.names)
    return out


def test_response_layout_does_not_import_ai_features():
    # O ciclo ai_features ↔ response_layout foi quebrado pelo catalog_format (F-BACKEND-1).
    assert "ai_features" not in _imported_modules("response_layout.py")


def test_catalog_format_stays_pure_formatting():
    # Se catalog_format passar a consultar catálogo, pedidos ou LLM, o ciclo volta pelos fundos.
    imported = _imported_modules("catalog_format.py")
    assert not imported & {"ai_features", "response_layout", "tools", "orders", "llm", "rag"}, imported
