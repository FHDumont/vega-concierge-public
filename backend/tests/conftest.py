"""Fixtures da suíte (F-BACKEND-1). Tudo offline: sem provider configurado a cascata cai no
stub determinístico-em-estrutura, então nenhum teste sem marker toca a rede."""
from __future__ import annotations

import os
import tempfile

# Precisa valer ANTES de importar `app.*` — `app.settings` resolve a config no import, e os
# módulos leem os valores dela também no import (mesma ordem dos antigos `run_*.py`).
os.environ.setdefault("DEPLOYMENT_ENVIRONMENT", "user-42")
# Sem arquivo de env: a suíte roda só com ambiente do SO + defaults, para não herdar as
# credenciais do `.env` de desenvolvimento da máquina (que a colocariam online).
os.environ.setdefault("VEGA_ENV_FILE", "")
# DB de teste isolado do `vega.db` real do dev (DT-036) — mesmo motivo de módulo-level das duas
# vars acima: `app/store/db.py` congela `DB_PATH = settings.orders_db` no import, tarde demais
# pra uma fixture. `setdefault` mantém a porta aberta pra um `ORDERS_DB` explícito do ambiente.
# Efeito colateral desejado: `llm/llm_config.py` deriva `.vega-persist` do `dirname(DB_PATH)`,
# então o diretório de persistência do dev também para de ser tocado pela suíte.
os.environ.setdefault("ORDERS_DB", os.path.join(tempfile.mkdtemp(), "vega.db"))

import pytest

from app.problems import FLAGS, ProblemFlags


@pytest.fixture(autouse=True)
def reset_problem_flags():
    """FLAGS é um singleton global (1 usuário por VM). Como os testes ligam toggles, devolve o
    estado original depois de cada um — senão um teste vaza problema injetado no seguinte."""
    saved = FLAGS.to_dict()
    yield FLAGS
    for name, value in saved.items():
        setattr(FLAGS, name, value)


@pytest.fixture
def clean_cache():
    """Zera o cache/limiter de LLM (F-022) antes e depois — ordem de teste não muda hit/miss."""
    from app.llm import llm_cache

    llm_cache.reset_state()
    yield llm_cache
    llm_cache.reset_state()


@pytest.fixture
def api_client():
    """TestClient da app real. Os bootstraps de import-time já rodaram no `import app.api`."""
    from fastapi.testclient import TestClient

    from app.api import app

    with TestClient(app) as client:
        yield client


def default_flags() -> dict:
    """Valores default de ProblemFlags — útil pra asserção de contrato de `/api/problems`."""
    return ProblemFlags().to_dict()
