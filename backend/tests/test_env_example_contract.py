"""`.env.example` 1:1 com `Settings.model_fields` (F-BACKEND-3, Etapa B.4, ADR-039).

O `.env` da raiz virou a fonte canônica de config por VM (Ansible escreve tudo nele — inclusive
os 3 tokens de LLM). Pra isso ser um contrato de verdade pro time de Ansible, `.env.example`
precisa listar TODA variável que `Settings` conhece — comentada quando o default já serve, viva
quando o workshop precisa dela — e nada além disso sem explicação. Este teste congela as duas
pontas: nenhum campo novo em `settings.py` pode ficar de fora do `.env.example` sem que alguém
note, e nenhuma variável nova em `.env.example` pode ser um typo silencioso de um campo que não
existe.
"""
from __future__ import annotations

import os
import re

from app.settings import Settings

_ENV_EXAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env.example",
)

# Variáveis legítimas de `.env.example` que NÃO são campos de `Settings` — compose (imagem/rede),
# Ops Console (`control/`), frontend (Next.js) e o interruptor de leitura do próprio `.env`
# (`VEGA_ENV_FILE`, settings.py). Nenhuma delas é lida por `app.settings.Settings`.
_NON_SETTINGS_VARS = frozenset({
    "IMAGE_OWNER", "IMAGE_TAG", "BACKEND_IMAGE",
    "CONTROL_PASSWORD", "CONTROL_TTYD_PORT",
    "PUBLIC_API_BASE", "API_INTERNAL_URL",
    "RAG_DB_USER", "RAG_DB_PASSWORD", "RAG_DB_NAME", "RAG_DB_PORT",
    "VEGA_ENV_FILE",
})

# Só casa `VAR=` no INÍCIO da porção útil da linha (opcionalmente comentada) — uma prosa como
# "# RAG_ENABLED — 0 ou `--no-rag` = keyword-only" não é uma declaração e não deve casar (por
# isso o formato de comentário deste arquivo usa em-dash `—`, nunca `=`, pra texto explicativo).
_VAR_DECL = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=")


def _declared_vars() -> list[str]:
    with open(_ENV_EXAMPLE, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    names = []
    for line in lines:
        m = _VAR_DECL.match(line)
        if m:
            names.append(m.group(1))
    return names


def test_every_settings_field_appears_exactly_once():
    declared = _declared_vars()
    counts: dict[str, int] = {}
    for name in declared:
        counts[name] = counts.get(name, 0) + 1

    settings_vars = {name.upper() for name in Settings.model_fields}
    missing = sorted(settings_vars - set(declared))
    assert not missing, f"campo(s) de Settings ausente(s) em .env.example: {missing}"

    duplicated = sorted(k for k, v in counts.items() if k in settings_vars and v > 1)
    assert not duplicated, f"campo(s) de Settings declarado(s) mais de uma vez: {duplicated}"


def test_no_stray_or_misspelled_variable():
    """Toda variável em `.env.example` é OU um campo de `Settings` OU está na allowlist de
    vars de compose/ops/frontend — nada sobra sem explicação (pega typo tipo `OPENAI_APIKEY`)."""
    settings_vars = {name.upper() for name in Settings.model_fields}
    declared = set(_declared_vars())
    stray = sorted(declared - settings_vars - _NON_SETTINGS_VARS)
    assert not stray, f"variável em .env.example sem campo Settings correspondente: {stray}"


def test_llm_token_specs_have_a_settings_field():
    """As 3 vars que `seed_providers_from_env` lê (`_ENV_SEED_SPECS`) são campos declarados de
    `Settings` — se o nome do campo mudar num refactor futuro, o seed quebra em silêncio sem
    este teste."""
    from app.llm.llm_config import _ENV_SEED_SPECS

    for spec in _ENV_SEED_SPECS:
        assert spec["env_field"] in Settings.model_fields, spec
