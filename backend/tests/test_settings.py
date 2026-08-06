"""Camada de config (F-BACKEND-1) — precedência e ausência de segredo no resumo de boot."""
from __future__ import annotations

import os

import pytest

from app.settings import Settings, settings


def test_os_environment_wins_over_the_env_file(monkeypatch, tmp_path):
    """Requisito das EC2s: o `.env` da AMI é o piso; o token injetado pelo Ansible tem de vencer."""
    env_file = tmp_path / ".env"
    env_file.write_text("GALILEO_PROJECT=do-arquivo\nOWNER_NAME=do-arquivo\n")

    monkeypatch.setenv("GALILEO_PROJECT", "do-ambiente")
    monkeypatch.delenv("OWNER_NAME", raising=False)

    resolved = Settings(_env_file=str(env_file))
    assert resolved.galileo_project == "do-ambiente"
    assert resolved.owner_name == "do-arquivo"


def test_env_file_wins_over_the_field_default(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_RESTOCK_AT=99\n")
    monkeypatch.delenv("ADMIN_RESTOCK_AT", raising=False)
    assert Settings(_env_file=str(env_file)).admin_restock_at == 99


def test_field_default_applies_with_no_environment_and_no_file(monkeypatch):
    monkeypatch.delenv("ADMIN_RESTOCK_AT", raising=False)
    assert Settings(_env_file=None).admin_restock_at == 3


def test_galileo_log_stream_accepts_both_spellings(monkeypatch):
    monkeypatch.delenv("GALILEO_LOG_STREAM", raising=False)
    monkeypatch.setenv("GALILEO_LOGSTREAM", "alias-antigo")
    assert Settings(_env_file=None).galileo_log_stream == "alias-antigo"


def test_summary_never_exposes_a_secret(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secreto")
    monkeypatch.setenv("OWNER_PASSWORD", "senha-do-dono")
    summary = Settings(_env_file=None).summary()

    assert summary["openai_api_key"] is True
    assert summary["owner_password"] is True
    assert "sk-super-secreto" not in str(summary)
    assert "senha-do-dono" not in str(summary)


def test_summary_covers_every_field():
    assert set(settings.summary()) == set(Settings.model_fields)


def test_env_file_defaults_to_the_repo_root(monkeypatch):
    from app.settings import _env_file_path

    monkeypatch.delenv("VEGA_ENV_FILE", raising=False)
    path = _env_file_path()
    assert os.path.basename(path) == ".env"
    assert os.path.basename(os.path.dirname(path)) == "vega-concierge"


def test_empty_vega_env_file_disables_file_loading(monkeypatch):
    # É o modo em que a suíte roda: só ambiente do SO + defaults.
    from app.settings import _env_file_path

    monkeypatch.setenv("VEGA_ENV_FILE", "")
    assert _env_file_path() is None


def test_vega_env_file_can_point_elsewhere(monkeypatch, tmp_path):
    from app.settings import _env_file_path

    monkeypatch.setenv("VEGA_ENV_FILE", str(tmp_path / "outro.env"))
    assert _env_file_path() == str(tmp_path / "outro.env")


def test_export_to_environ_fills_what_third_party_sdks_read(monkeypatch, tmp_path):
    """O SDK do Galileo lê `os.environ` sozinho — um valor que só existisse no `.env` deixaria a
    app se dar por habilitada e o SDK falhar na credencial."""
    env_file = tmp_path / ".env"
    env_file.write_text("GALILEO_API_KEY=do-arquivo\n")
    monkeypatch.delenv("GALILEO_API_KEY", raising=False)

    resolved = Settings(_env_file=str(env_file))
    assert "GALILEO_API_KEY" in resolved.export_to_environ()
    assert os.environ["GALILEO_API_KEY"] == "do-arquivo"


def test_export_to_environ_never_overwrites_the_os_environment(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GALILEO_PROJECT=do-arquivo\n")
    monkeypatch.setenv("GALILEO_PROJECT", "do-ambiente")

    Settings(_env_file=str(env_file)).export_to_environ()
    assert os.environ["GALILEO_PROJECT"] == "do-ambiente"


# --- tolerância a valor mal formado ------------------------------------------
# A `Settings` é a lista de variáveis que o time de Ansible renderiza nas 150 EC2s. Um campo
# numérico em branco ou com lixo não pode derrubar a instância no import.

@pytest.mark.parametrize("raw", ["", "   ", "muitos"])
def test_broken_numeric_value_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("ADMIN_RESTOCK_AT", raw)
    assert Settings(_env_file=None).admin_restock_at == 3


@pytest.mark.parametrize("raw", ["", "sim"])
def test_broken_boolean_value_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("LLM_CACHE_ENABLED", raw)
    assert Settings(_env_file=None).llm_cache_enabled is True


def test_blank_text_field_stays_blank(monkeypatch):
    # Campo de texto é diferente: `GALILEO_API_KEY=` vazio quer dizer vazio, não default.
    monkeypatch.setenv("GALILEO_API_KEY", "")
    assert Settings(_env_file=None).galileo_api_key == ""


def test_a_valid_value_is_still_honoured(monkeypatch):
    monkeypatch.setenv("ADMIN_RESTOCK_AT", "9")
    assert Settings(_env_file=None).admin_restock_at == 9
