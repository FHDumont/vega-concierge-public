"""Configuração da app — fonte única, **ambiente-primeiro** (F-BACKEND-1).

Precedência (nativa do pydantic-settings, e é ela que importa aqui):

    variável de ambiente do SO  >  arquivo `.env` da raiz do repo  >  default do campo

As EC2s do workshop são clonadas de uma AMI com o `.env` já embutido, e o time de Ansible
injeta os tokens reais **no ambiente do SO** de cada máquina. Por isso o ambiente tem de vencer
o arquivo: o `.env` da imagem é o piso, não o teto.

Cada default aqui é **o mesmo** que estava no `os.getenv` do módulo de origem — a linha de
comentário diz de onde veio. Nada de valor novo: esta fase só muda ONDE a config é lida.

Uso:

    from .settings import settings
    GOLD_THRESHOLD = settings.tier_gold_usd
"""
from __future__ import annotations

import logging
import os

from pydantic import AliasChoices, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger(__name__)

# O `.env` mora na raiz do repo (é o mesmo que `scripts/*.sh` e o docker compose consomem),
# não em `backend/`.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_file_path() -> str | None:
    """Qual `.env` carregar. `VEGA_ENV_FILE` aponta para outro arquivo; `VEGA_ENV_FILE=""`
    desliga a leitura de arquivo e deixa só ambiente do SO + defaults — é assim que a suíte de
    testes roda, para não herdar as credenciais do `.env` de desenvolvimento da máquina."""
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

    # --- identidade do deploy (api.py, runnable_config.py, hub/hub.py) ---------
    deployment_environment: str = "local-dev"
    vega_version: str = "dev"
    vega_git_sha: str = Field(default="local", validation_alias=AliasChoices("VEGA_GIT_SHA"))
    vega_build_date: str = ""

    # --- persistência (store/db.py, llm/llm_config.py) ------------------------
    orders_db: str = os.path.join(_BACKEND_ROOT, "vega.db")
    vega_persist_dir: str = ""

    # --- ciclo de vida do pedido (store/orders.py) ----------------------------
    order_ship_after_s: int = 30
    order_deliver_after_s: int = 90

    # --- usuários, tiers e auth (store/users.py) ------------------------------
    tier_gold_usd: float = 1000
    tier_platinum_usd: float = 5000
    auth_pbkdf2_iterations: int = 120000
    owner_email: str = "fernando@fernando.com.br"
    owner_password: str = "owner1234"  # default de DEMO — DT-012
    owner_name: str = "Fernando (Owner)"

    # --- regras de negócio (store/tools.py, features/*.py) --------------------
    refund_window_days: int = 30
    admin_insights_window_days: int = 7
    admin_restock_at: int = 3

    # --- gateway de pagamento simulado (store/payments.py) --------------------
    payment_latency_ms: float = 400
    payment_fail_rate: float = 0.0
    payment_latency_spike_ms: float = 1500

    # --- LLM: cascata, cache e inspector --------------------------------------
    llm_timeout_s: float = 20
    llm_stub_model: str = "gpt-4o-mini"
    llm_provider_prompt_cache: bool = False
    llm_cache_enabled: bool = True
    llm_cache_ttl_s: float = 300
    llm_cache_max: int = 256
    llm_rate_max: int = 30            # chamadas reais ao provider...
    llm_rate_window_s: float = 60     # ...por janela (s); <=0 desliga
    llm_activity_max: int = 200

    # --- Ollama do host (llm/llm_config.py, features/rag.py, api.py) ----------
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_chat_model: str = "llama3.2"

    # --- RAG (features/rag.py) ------------------------------------------------
    rag_enabled: bool = False
    rag_database_url: str = ""
    rag_top_k: int = 3
    rag_embedding_provider: str = "ollama"
    # Sem valor explícito o default depende do provider ("nomic-embed-text" no Ollama,
    # "text-embedding-3-small" na OpenAI), então o campo nasce vazio e cada chamador aplica o
    # SEU default — é a única forma de preservar o comportamento antigo num campo só.
    rag_embedding_model: str = ""

    # --- observabilidade / Agent Control (obs/galileo_*.py) --------------------
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

    # --- simulador em modo browser (sim/sim_browser.py) -----------------------
    sim_browser_base_url: str = "http://localhost:3000"

    # --- tokens de provider injetados pelo ambiente do SO ---------------------
    # Reservados para a F-BACKEND-3 (bootstrap da cascata a partir do ambiente). Declarados
    # desde já para que a lista de variáveis entregue ao time de Ansible seja esta classe.
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    aws_bearer_token_bedrock: str = ""
    aws_default_region: str = "us-east-1"

    # --- tolerância a valor mal formado ---------------------------------------

    @field_validator("*", mode="before")
    @classmethod
    def _blank_falls_back_to_default(cls, value, info):
        """Variável NUMÉRICA ou BOOLEANA declarada vazia (`ADMIN_RESTOCK_AT=`) volta ao default.

        Antes desta fase cada `os.getenv` tinha o seu default e uma string vazia simplesmente
        não parseava para número — alguns pontos já traziam `try/except` por isso. Com a
        tipagem do pydantic, um vazio viraria `ValidationError` **no import**, e a app não
        subiria. Um template de Ansible que renderiza um campo em branco numa das EC2s não
        pode derrubar a instância: cai no default, que é justamente o valor bom.

        Campo de texto NÃO entra aqui: `GALILEO_API_KEY=` vazio quer dizer vazio mesmo.
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
            # Valor presente mas impossível de converter (`ADMIN_RESTOCK_AT=muitos`). Mesma
            # razão do validator acima: preferimos o default e um aviso a uma VM que não sobe.
            bad = {str(err["loc"][0]) for err in exc.errors() if err.get("loc")}
            for name in bad:
                kwargs[name] = type(self).model_fields[name].get_default(
                    call_default_factory=True,
                )
            super().__init__(**kwargs)
            _log.warning(
                "config: valor inválido em %s — usando o default do campo", ", ".join(sorted(bad)),
            )

    # --- apresentação ---------------------------------------------------------

    _SECRET_FIELDS = frozenset({
        "owner_password", "galileo_api_key", "enroll_token", "openai_api_key",
        "anthropic_api_key", "aws_bearer_token_bedrock", "rag_database_url",
    })

    def summary(self) -> dict[str, object]:
        """Config resolvida para o print de boot. Segredo nunca aparece: vira `True`/`False`
        (definido ou não), porque o que se quer saber no telão é se a credencial chegou."""
        out: dict[str, object] = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            out[name] = bool(value) if name in self._SECRET_FIELDS else value
        return out

    def summary_lines(self) -> list[str]:
        return [f"  {k} = {v}" for k, v in sorted(self.summary().items())]

    # --- consistência com SDKs de terceiro -------------------------------------

    # SDKs que leem `os.environ` por conta própria (galileo, agent-control, openai, boto3). Sem
    # isto, um valor que só existe no `.env` seria visto pela app e NÃO pelo SDK — a app se
    # daria por habilitada e o SDK falharia na credencial. Nunca sobrescreve o ambiente do SO
    # (que já venceu na resolução): só preenche o que está faltando.
    _EXPORTED_TO_ENVIRON = (
        "GALILEO_API_KEY", "GALILEO_PROJECT", "GALILEO_LOG_STREAM", "GALILEO_CONSOLE_URL",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_BEARER_TOKEN_BEDROCK", "AWS_DEFAULT_REGION",
    )

    def export_to_environ(self) -> list[str]:
        """Publica no `os.environ` o que os SDKs de terceiro leem sozinhos. Devolve os nomes
        efetivamente exportados (o resto já estava definido no ambiente ou está vazio)."""
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
