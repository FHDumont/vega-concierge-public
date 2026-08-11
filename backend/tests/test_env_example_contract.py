"""`.env.example` 1:1 with `Settings.model_fields` (F-BACKEND-3, Step B.4, ADR-039).

The root `.env` became the canonical per-VM config source (Ansible writes everything into it —
including the 3 LLM tokens). For this to be a source-of-truth contract for the Ansible team,
`.env.example` must list EVERY variable that `Settings` knows about — commented out when the
default already works, live when the workshop needs it — and nothing beyond that without
explanation. This test freezes both ends: no new field in `settings.py` can be left out of
`.env.example` without someone noticing, and no new variable in `.env.example` can be a silent
typo of a field that doesn't exist.
"""
from __future__ import annotations

import os
import re

from app.settings import Settings

_ENV_EXAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env.example",
)

# Legitimate `.env.example` variables that are NOT `Settings` fields — compose (image/network),
# Ops Console (`control/`), frontend (Next.js), and the switch that reads `.env` itself
# (`VEGA_ENV_FILE`, settings.py). None of these are read by `app.settings.Settings`.
_NON_SETTINGS_VARS = frozenset({
    "IMAGE_OWNER", "IMAGE_TAG", "BACKEND_IMAGE",
    "CONTROL_PASSWORD", "CONTROL_TTYD_PORT",
    "PUBLIC_API_BASE", "API_INTERNAL_URL",
    "RAG_DB_USER", "RAG_DB_PASSWORD", "RAG_DB_NAME", "RAG_DB_PORT",
    "VEGA_ENV_FILE",
})

# Only matches `VAR=` at the START of the useful part of the line (optionally commented) — prose
# like "# RAG_ENABLED — 0 or `--no-rag` = keyword-only" is not a declaration and must not match
# (that's why this file's comment format uses an em-dash `—`, never `=`, for explanatory text).
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
    assert not missing, f"Settings field(s) missing from .env.example: {missing}"

    duplicated = sorted(k for k, v in counts.items() if k in settings_vars and v > 1)
    assert not duplicated, f"Settings field(s) declared more than once: {duplicated}"


def test_no_stray_or_misspelled_variable():
    """Every variable in `.env.example` is EITHER a `Settings` field OR is on the
    compose/ops/frontend allowlist — nothing left unexplained (catches typos like `OPENAI_APIKEY`)."""
    settings_vars = {name.upper() for name in Settings.model_fields}
    declared = set(_declared_vars())
    stray = sorted(declared - settings_vars - _NON_SETTINGS_VARS)
    assert not stray, f"variable in .env.example with no matching Settings field: {stray}"


def test_llm_token_specs_have_a_settings_field():
    """The 3 vars that `seed_providers_from_env` reads (`_ENV_SEED_SPECS`) are declared fields of
    `Settings` — if the field name changes in a future refactor, the seed breaks silently without
    this test."""
    from app.llm.llm_config import _ENV_SEED_SPECS

    for spec in _ENV_SEED_SPECS:
        assert spec["env_field"] in Settings.model_fields, spec
