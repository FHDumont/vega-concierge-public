"""App configuration — single source, **environment-first** (F-BACKEND-1).

Precedence (native to pydantic-settings, and that's what matters here):

    OS environment variable  >  `.env` file from repo root  >  field default

Workshop EC2s are cloned from an AMI with `.env` already embedded, and the Ansible team
injects real tokens **into the OS environment** of each machine. That's why the environment must win
over the file: the image's `.env` is the floor, not the ceiling.

Each default here is **the same** as what was in `os.getenv` of the original module — the comment line
says where it came from. Nothing new value-wise: this phase only changes WHERE config is read.

Usage:

    from .settings import settings
    GOLD_THRESHOLD = settings.tier_gold_usd
"""
from __future__ import annotations

import logging
import os

from pydantic import AliasChoices, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger(__name__)

# The `.env` lives in the repo root (same as `scripts/*.sh` and docker compose consume),
# not in `backend/`.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _env_file_path() -> str | None:
    """Which `.env` to load. `VEGA_ENV_FILE` points to another file; `VEGA_ENV_FILE=""`
    disables file reading and leaves only OS environment + defaults — this is how the test suite
    runs, to avoid inheriting credentials from the machine's development `.env`."""
    override = os.environ.get("VEGA_ENV_FILE")
    if override is not None:
        return override or None
    return os.path.join(_REPO_ROOT, ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- deployment identity (api.py, runnable_config.py, hub/hub.py) ---------
    deployment_environment: str = "local-dev"
    vega_version: str = "dev"
    vega_git_sha: str = Field(default="local", validation_alias=AliasChoices("VEGA_GIT_SHA"))
    vega_build_date: str = ""

    # --- persistence (store/db.py, llm/llm_config.py) ------------------------
    orders_db: str = os.path.join(_BACKEND_ROOT, "vega.db")
    vega_persist_dir: str = ""

    # --- order lifecycle (store/orders.py) ----------------------------
    order_ship_after_s: int = 30
    order_deliver_after_s: int = 90

    # --- users, tiers and auth (store/users.py) ------------------------------
    tier_gold_usd: float = 1000
    tier_platinum_usd: float = 5000
    auth_pbkdf2_iterations: int = 120000
    owner_email: str = "fernando@fernando.com.br"
    owner_password: str = "owner1234"  # DEMO default — DT-012
    owner_name: str = "Fernando (Owner)"

    # --- business rules (store/tools.py, features/*.py) --------------------
    refund_window_days: int = 30
    admin_insights_window_days: int = 7
    admin_restock_at: int = 3

    # --- simulated payment gateway (store/payments.py) --------------------
    payment_latency_ms: float = 400
    payment_fail_rate: float = 0.0
    payment_latency_spike_ms: float = 1500

    # --- LLM: cascade, cache and inspector --------------------------------------
    llm_timeout_s: float = 20
    llm_stub_model: str = "gpt-4o-mini"
    llm_provider_prompt_cache: bool = False
    llm_cache_enabled: bool = True
    llm_cache_ttl_s: float = 300
    llm_cache_max: int = 256
    llm_rate_max: int = 30            # real calls to provider...
    llm_rate_window_s: float = 60     # ...per window (s); <=0 disables
    llm_activity_max: int = 200

    # --- HTTP rate limit at the edge (F-WORKSHOP-GUARD) ----------------------------
    api_rate_enabled: bool = True
    api_rate_ai_max: int = 12
    api_rate_ai_window_s: float = 60
    api_rate_default_max: int = 60
    api_rate_default_window_s: float = 60

    # --- Host Ollama (llm/llm_config.py, features/rag.py, api.py) ----------
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_chat_model: str = "llama3.2"

    # --- default models of cloud cascade (llm/llm_config.py — auto-register on boot) -----------
    openai_chat_model: str = "gpt-4o-mini"
    anthropic_chat_model: str = "claude-sonnet-4-5"
    bedrock_chat_model: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

    # --- RAG (features/rag.py) ------------------------------------------------
    rag_enabled: bool = False
    rag_database_url: str = ""
    rag_top_k: int = 3
    rag_embedding_provider: str = "ollama"
    # Without explicit value the default depends on provider ("nomic-embed-text" on Ollama,
    # "text-embedding-3-small" on OpenAI), so the field is born empty and each caller applies their
    # OWN default — it's the only way to preserve the old behavior in one field.
    rag_embedding_model: str = ""

    # --- observability / Agent Control (obs/galileo_*.py) --------------------
    galileo_api_key: str = ""
    galileo_project: str = "vega-concierge"
    galileo_log_stream: str = Field(
        default="default",
        validation_alias=AliasChoices("GALILEO_LOG_STREAM", "GALILEO_LOGSTREAM"),
    )
    galileo_console_url: str = "https://console.multitenant.galileocloud.io"
    agent_control_url: str = ""
    agent_control_api_key_header: str = "Galileo-API-Key"
    vega_session_idle_minutes: int = 5

    # --- hub / enrollment (hub/enroll.py) -------------------------------------
    enroll_token: str = ""

    # --- simulator in browser mode (sim/sim_browser.py) -----------------------
    sim_browser_base_url: str = "http://localhost:3000"

    # --- provider tokens injected by OS environment ---------------------
    # Reserved for F-BACKEND-3 (cascade bootstrap from environment). Declared
    # upfront so the list of variables delivered to the Ansible team is this class.
    llm_provider_priority: str = "BEDROCK,OPENAI,ANTHROPIC,OLLAMA"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    aws_bearer_token_bedrock: str = ""
    aws_default_region: str = "us-east-1"

    # --- malformed value tolerance ---------------------------------------

    @field_validator("*", mode="before")
    @classmethod
    def _blank_falls_back_to_default(cls, value, info):
        """NUMERIC or BOOLEAN variable declared empty (`ADMIN_RESTOCK_AT=`) falls back to default.

        Before this phase each `os.getenv` had its own default and an empty string simply
        didn't parse to a number — some places already had `try/except` for this. With
        pydantic's typing, an empty value would become `ValidationError` **on import**, and the app wouldn't
        start. An Ansible template that renders a blank field on one of the EC2s
        can't bring down the instance: it falls to the default, which is exactly the right value.

        Text field does NOT come here: `GALILEO_API_KEY=` empty means empty.
        """
        if not isinstance(value, str) or value.strip():
            return value
        field = cls.model_fields.get(info.field_name)
        if field is None or field.annotation is str:
            return value
        return field.get_default(call_default_factory=True)

    def __init__(self, **kwargs):
        try:
            super().__init__(**kwargs)
        except ValidationError as exc:
            # Value present but impossible to convert (`ADMIN_RESTOCK_AT=many`). Same
            # reason as the validator above: we prefer the default and a warning to a VM that won't start.
            bad = {str(err["loc"][0]) for err in exc.errors() if err.get("loc")}
            for name in bad:
                kwargs[name] = type(self).model_fields[name].get_default(
                    call_default_factory=True,
                )
            super().__init__(**kwargs)
            _log.warning(
                "config: invalid value in %s — using field default", ", ".join(sorted(bad)),
            )

    # --- presentation ---------------------------------------------------------

    _SECRET_FIELDS = frozenset({
        "owner_password", "galileo_api_key", "enroll_token", "openai_api_key",
        "anthropic_api_key", "aws_bearer_token_bedrock", "rag_database_url",
    })

    def summary(self) -> dict[str, object]:
        """Resolved config for boot print. Secrets never appear: become `True`/`False`
        (defined or not), because what matters on the screen is whether the credential arrived."""
        out: dict[str, object] = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            out[name] = bool(value) if name in self._SECRET_FIELDS else value
        return out

    def summary_lines(self) -> list[str]:
        return [f"  {k} = {v}" for k, v in sorted(self.summary().items())]

    # --- consistency with third-party SDKs -------------------------------------

    # SDKs that read `os.environ` on their own (galileo, agent-control, openai, boto3). Without
    # this, a value that only exists in `.env` would be seen by the app and NOT by the SDK — the app would
    # consider itself enabled and the SDK would fail on the credential. Never overwrites the OS environment
    # (which already won on resolution): just fills in what's missing.
    _EXPORTED_TO_ENVIRON = (
        "GALILEO_API_KEY", "GALILEO_PROJECT", "GALILEO_LOG_STREAM", "GALILEO_CONSOLE_URL",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_BEARER_TOKEN_BEDROCK", "AWS_DEFAULT_REGION",
    )

    def export_to_environ(self) -> list[str]:
        """Publishes to `os.environ` what third-party SDKs read on their own. Returns the names
        actually exported (the rest already existed in the environment or is empty)."""
        exported = []
        for var in self._EXPORTED_TO_ENVIRON:
            if os.environ.get(var):
                continue
            value = getattr(self, var.lower(), "")
            if value:
                os.environ[var] = str(value)
                exported.append(var)
        return exported


settings = Settings()
settings.export_to_environ()
