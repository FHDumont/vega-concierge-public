"""Fixtures da suíte (F-BACKEND-1). Tudo offline: sem provider configurado a cascata cai no
stub determinístico-em-estrutura, então nenhum teste sem marker toca a rede."""
from __future__ import annotations

import os

# Precisa valer ANTES de importar `app.*` — vários módulos leem env no import (mesma ordem dos
# antigos `run_*.py`).
os.environ.setdefault("DEPLOYMENT_ENVIRONMENT", "user-42")

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
    from app import llm_cache

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
