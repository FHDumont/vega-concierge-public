"""API FastAPI do Vega Concierge. Loja + Behind the Scenes.

Este módulo só MONTA a app: bootstrap do estado no `lifespan`, CORS e registro dos routers.
Cada rota mora no router do seu domínio, em `app/routers/` — nenhum router usa `prefix`, então o
path completo continua escrito na própria rota (o contrato com o frontend é congelado:
`CONVENCOES.md` §NÃO mude).

O bootstrap roda no **startup**, não no import. Importar `app.api` (para inspecionar rotas, por
exemplo) não toca mais no SQLite nem inicializa o Agent Control; quem faz isso é subir a app.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import (
    agent_config,
    feature_flags,
    galileo_control,
    hub,
    hub_settings,
    llm_config,
    orders,
    rum,
    users,
)
from .routers import ROUTERS
from .settings import settings
from .tools import seed_workshop_stock

log = logging.getLogger(__name__)


def _bootstrap() -> None:
    """Estado que a app precisa ter de pé antes do primeiro request. Tudo idempotente — roda
    igual num boot novo, num restart e a cada recarga do `--reload`."""
    log.info("config resolvida (segredo aparece só como True/False):\n%s",
             "\n".join(settings.summary_lines()))

    orders.init_db()  # create_all no boot (ADR-006)
    users.init_db()   # tabela de usuários (F-008) + papel OWNER (F-020)
    seed_workshop_stock()  # estoque alto no boot; NS-005/NS-022 esgotados de demo
    users.seed_demo_user()   # usuário de teste de DEMO + histórico → tier GOLD (idempotente; F-010)
    users.seed_owner_user()  # usuário OWNER (config de LLM owner-only; idempotente; F-020)
    llm_config.init_db()     # tabela de provedores de LLM (F-020)
    llm_config.restore_providers_backup()  # fresh-state preserva cascata LLM (F-REAL-ENV-1)
    llm_config.seed_ollama_default()  # Ollama Local se vazio (F-REAL-ENV-1)
    agent_config.init_db()      # tabela de config por agente (F-021)
    agent_config.seed_defaults()  # semeia os 6 agentes com os prompts atuais (idempotente; F-021)
    agent_config.migrate_f052_prompts()  # prompts pré-F-052 no SQLite → chatbot (F-052)
    hub_settings.init_db()      # tabela de fonte local|remote (hub/peer — F-026)
    feature_flags.init_db()     # tabela de feature flags de menu/superfícies (F-033)
    rum.init_db()               # tabela de config do Splunk RUM (snippet + toggle — F-040-RUM)
    hub.apply_source()          # instala a ConfigSource ativa conforme os settings (F-026)
    galileo_control.init_once()  # Agent Control / Protect (F-GALILEO-2, ADR-033)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _bootstrap()
    yield


app = FastAPI(title="Vega Concierge API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for _router in ROUTERS:
    app.include_router(_router)
