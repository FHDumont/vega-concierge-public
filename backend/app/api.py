"""API FastAPI do Vega Concierge. Loja + Behind the Scenes."""
import os
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal
from pydantic import BaseModel
from .problems import FLAGS, UC_PRESETS, apply_preset
from . import galileo_obs
from . import orders, users, admin, llm_config, agent_config, llm_activity, hub, hub_settings, topology, enroll
from . import feature_flags, rum
from .agents import arun_workflow, arun_chat_workflow
from . import checkout, simulator, llm, ai_features, compare, returns
from .runnable_config import ai_request_scope
from . import galileo_control
from .tools import seed_workshop_stock

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

app = FastAPI(title="Vega Concierge API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class RunRequest(BaseModel):
    request: str = "a birthday gift under $300"

class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str

class ChatContextIn(BaseModel):
    sku: str | None = None
    order_id: str | None = None

class ChatRequest(BaseModel):
    messages: list[ChatMessageIn]
    context: ChatContextIn | None = None

class ProductQARequest(BaseModel):
    sku: str
    question: str = ""
class ProductDescribeRequest(BaseModel):
    sku: str
class SemanticSearchRequest(BaseModel):
    query: str
class CompareRequest(BaseModel):
    # Compare 2 produtos (F-029): coordinator → comparator + tools (dados reais).
    sku_a: str
    sku_b: str
class HomePicksRequest(BaseModel):
    # IA-Home (F-023): recomendações personalizadas; `favorites` (skus) enviesa os picks (opcional).
    favorites: list[str] = []
class CartCrossSellRequest(BaseModel):
    # IA-Carrinho (F-023): cross-sell a partir dos SKUs no carrinho atual.
    skus: list[str] = []
class GiftMessageRequest(BaseModel):
    # IA-Checkout (F-024): breve input (ocasião/destinatário/tom) → mensagem de presente gerada.
    brief: str = ""
class OrderItemIn(BaseModel):
    sku: str
    name: str
    qty: int
    price: float
class CustomerIn(BaseModel):
    name: str
    email: str
    address: str
class CreateOrderRequest(BaseModel):
    items: list[OrderItemIn]
    customer: CustomerIn
class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str
class LoginRequest(BaseModel):
    email: str
    password: str
class UpdateMeRequest(BaseModel):
    address: str
class ProblemUpdate(BaseModel):
    price_hallucination: bool | None = None
    fraud_false_positive: bool | None = None
    inventory_outage: bool | None = None
    latency_spike: bool | None = None
    cost_spike: bool | None = None
    payment_outage: bool | None = None
    payment_latency: bool | None = None
    refund_false_denial: bool | None = None  # F-029: nega um reembolso elegível (erro do agente)
    prompt_injection: bool | None = None  # UC-4: agente aceita override de preço/política do comprador
    active_scenario: str | None = None  # preset UC ativo (uc-1..uc-5); "" limpa
class InspectorToggle(BaseModel):
    # Liga/desliga o LLM Inspector (F-023; owner-only).
    enabled: bool
class RumIn(BaseModel):
    # Edição parcial da config do Splunk RUM (F-040-RUM; owner-only). None = não mexe.
    enabled: bool | None = None
    snippet: str | None = None
class FlagsIn(BaseModel):
    # Edição parcial das feature flags de menu (F-033; owner-only). None = não mexe.
    behind_the_scenes: bool | None = None
    admin: bool | None = None
    simulator: bool | None = None
    inspector: bool | None = None
class SimStartRequest(BaseModel):
    # Config do simulador avançado (F-018). Tudo opcional → defaults/clamps em SimConfig.from_dict.
    mode: str | None = None                  # api | browser (F-039): API in-process vs navegador real
    concurrency: int | None = None          # N: tamanho do pool E nº de jornadas concorrentes
    wait_min_s: float | None = None         # espera entre jornadas (slot ocioso)
    wait_max_s: float | None = None
    think_min_s: float | None = None        # think-time entre ações
    think_max_s: float | None = None
    actions_min: int | None = None          # nº de ações de navegação por jornada
    actions_max: int | None = None
    concierge_pct: int | None = None        # % de jornadas que usam o Concierge
    problem_pct: int | None = None          # % de jornadas que injetam um problema
    problems: list[str] | None = None       # quais problemas elegíveis p/ injeção
    category_mix: dict[str, int] | None = None  # peso por categoria no carrinho
    tier_mix: dict[str, int] | None = None      # distribuição de tier dos usuários criados
    speed: float | None = None              # multiplicador dos sleeps (<1 = demo rápido)
    target_kind: str | None = None          # none | orders | duration
    target_value: int | None = None         # nº de pedidos OU segundos
    reset: bool | None = None               # limpar pedidos antes de iniciar
    max_lines: int | None = None
    max_qty: int | None = None
class SimPauseRequest(BaseModel):
    paused: bool = True
class ProviderIn(BaseModel):
    # Cria um provider da cascata de LLM (config owner-only — F-020). `api_key` é segredo
    # (write-only; nunca volta ao front). `kind`: openai | anthropic | bedrock.
    name: str
    kind: str = "openai"
    base_url: str = ""
    model: str
    api_key: str = ""
    enabled: bool = True
class ProviderUpdate(BaseModel):
    # Atualização parcial. `api_key` vazio/omitido MANTÉM a chave atual (write-only).
    name: str | None = None
    kind: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    order: int | None = None
class ReorderIn(BaseModel):
    ids: list[str]
class TestProviderIn(BaseModel):
    # Test "ao vivo" de um provider ainda não salvo (UI). Se vier vazio, testa o salvo por id.
    name: str | None = None
    kind: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
class AgentUpdate(BaseModel):
    # Config por agente (F-021). Campos parciais; None mantém. Sem segredo (vai cru ao front).
    connection: str | None = None   # provider id (LP-xxxx) ou '' = cascata completa
    model: str | None = None        # override opcional do modelo
    role: str | None = None
    system_prompt: str | None = None
class AgentTestIn(BaseModel):
    # Test "ao vivo" de um agente: edições opcionais sobre o salvo → 1 chamada real ao LLM resolvido.
    connection: str | None = None
    model: str | None = None
    role: str | None = None
    system_prompt: str | None = None
class HubSourceIn(BaseModel):
    # Fonte de config local|remote (hub/peer — F-026). Tokens são write-only (segredo;
    # nunca voltam ao front). `serve_token` aceita '' explícito (owner desliga o servir).
    source: str | None = None             # local | remote
    hub_url: str | None = None            # URL do hub (lado cliente)
    enrollment_token: str | None = None   # token p/ puxar do hub (write-only)
    pull_interval_s: int | None = None
    serve_token: str | None = None        # token exigido p/ servir como hub
class EnrollIn(BaseModel):
    # Enroll RECEBIDO (lado cliente — F-027). Máquina-a-máquina: o hub manda a própria URL +
    # o token p/ esta loja puxar a config. Gateado por ENROLL_TOKEN (segredo do lab), não owner.
    hub_url: str
    enrollment_token: str = ""
    pull_interval_s: int | None = None
class EnrollPushIn(BaseModel):
    # Enroll PUSH (lado hub — F-027, owner-only): força N lojas (por IP) a virar clientes deste hub.
    ips: list[str]
    hub_url: str                          # URL deste hub (como os alvos o alcançam)
    enroll_token: str                     # segredo compartilhado p/ autenticar nos alvos (ENROLL_TOKEN deles)
    enrollment_token: str                 # token que os alvos usarão p/ puxar (= serve_token deste hub)
    pull_interval_s: int | None = None

def _ollama_health() -> dict:
    """Probe Ollama /api/tags — host-side LLM/embeddings (F-REAL-ENV-1)."""
    import json
    import urllib.error
    import urllib.request

    base = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434").rstrip("/")
    embed_model = os.getenv("RAG_EMBEDDING_MODEL", "nomic-embed-text")
    chat_model = os.getenv("OLLAMA_CHAT_MODEL", "llama3.2")
    out = {
        "base_url": base,
        "reachable": False,
        "embed_model": embed_model,
        "chat_model": chat_model,
        "embed_model_present": False,
        "chat_model_present": False,
    }
    try:
        req = urllib.request.Request(f"{base}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            payload = json.loads(resp.read().decode())
        names = {m.get("name", "").split(":")[0] for m in payload.get("models", [])}
        out["reachable"] = True
        embed_base = embed_model.split(":")[0]
        chat_base = chat_model.split(":")[0]
        out["embed_model_present"] = embed_base in names
        out["chat_model_present"] = chat_base in names
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
        pass
    return out


@app.get("/api/health")
def health():
    from . import rag

    return {
        "status": "ok",
        "version": os.getenv("VEGA_VERSION", "dev"),
        "git_sha": os.getenv("VEGA_GIT_SHA", "local"),
        "build_date": os.getenv("VEGA_BUILD_DATE") or None,
        "environment": os.getenv("DEPLOYMENT_ENVIRONMENT", "local-dev"),
        "rag": {
            "enabled": rag.is_pgvector_enabled(),
            "backend": rag.backend_name(),
            "embedding_provider": rag.embedding_provider(),
        },
        "ollama": _ollama_health(),
        "llm_providers": len(llm_config.list_providers()),
    }

@app.get("/api/catalog")
def catalog():
    from .tools import _active_catalog
    return _active_catalog()

@app.get("/api/policies")
def policies():
    from . import rag
    return {"policies": rag.load_policy_files()}

@app.get("/api/problems")
def get_problems():
    return FLAGS.to_dict()

@app.put("/api/problems")
def set_problems(p: ProblemUpdate):
    for k, v in p.model_dump(exclude_none=True).items():
        setattr(FLAGS, k, v)
    # Desligou todos os flags do preset ativo → limpa cenário (evita UC fantasma após refresh).
    if FLAGS.active_scenario and FLAGS.active_scenario in UC_PRESETS:
        keys = UC_PRESETS[FLAGS.active_scenario]
        if not any(getattr(FLAGS, name) for name in keys):
            FLAGS.active_scenario = ""
    return FLAGS.to_dict()

@app.post("/api/problems/preset/{preset_id}")
def apply_problem_preset(preset_id: str):
    try:
        return apply_preset(preset_id).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@app.get("/api/galileo/config")
def galileo_public_config():
    return galileo_obs.public_config()

@app.post("/api/run")
async def run(req: RunRequest, authorization: str | None = Header(default=None),
              x_vega_session: str | None = Header(default=None)):
    user_id = _optional_user_id(authorization)
    error = None
    try:
        with ai_request_scope(feature="concierge", session_id=x_vega_session, user_id=user_id) as config:
            final = await arun_workflow(req.request, config=config)
    except Exception as e:
        final = {"trace": ["Algo deu errado. Tente novamente."], "quality": None}
        error = str(e)
    return {
        "messages": final.get("trace", []),
        "quality": final.get("quality"),
        "recommended": final.get("selected"),  # produto escolhido pelo agente (vitrine)
        "answer": final.get("answer"),          # recomendação composta pelo LLM, exibida (F-025)
        "language": final.get("language"),      # idioma detectado/usado na resposta (F-025)
        "order": final.get("order"),
        "error": error,
    }


@app.post("/api/chat")
async def chat(req: ChatRequest, authorization: str | None = Header(default=None),
             x_vega_session: str | None = Header(default=None)):
    user_id = _optional_user_id(authorization)
    error = None
    ctx = req.context.model_dump(exclude_none=True) if req.context else {}
    try:
        with ai_request_scope(feature="chat", session_id=x_vega_session, user_id=user_id) as config:
            final = await arun_chat_workflow(
                [m.model_dump() for m in req.messages],
                context=ctx,
                config=config,
            )
    except Exception as e:
        final = {"answer": "Something went wrong. Please try again.", "intent": "error",
                 "artifacts": {}, "language": None, "trace": []}
        error = str(e)
    return {
        "reply": final.get("answer"),
        "intent": final.get("intent"),
        "artifacts": final.get("artifacts") or {},
        "language": final.get("language"),
        "llm_unavailable": bool(final.get("llm_unavailable")),
        "error": error,
    }


# --- IA-Produto (F-022, etapa 2) --------------------------------------------
# Features de IA da página de detalhe: Q&A fundamentado nos dados do produto e descrição gerada
# (cacheada por produto). Cada uma passa pelo controle de custo. Honram os toggles de problema
# (hallucination/cost/latency). Standalone (stub) devolve fallback gracioso.

@app.post("/api/product/qa")
def product_qa(req: ProductQARequest, x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="product_qa", session_id=x_vega_session):
        ans = ai_features.product_qa(req.sku, req.question)
    if ans is None:
        raise HTTPException(status_code=404, detail="product not found")
    return ans

@app.post("/api/product/describe")
def product_describe(req: ProductDescribeRequest, x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="product_desc", session_id=x_vega_session):
        desc = ai_features.product_describe(req.sku)
    if desc is None:
        raise HTTPException(status_code=404, detail="product not found")
    return desc


# --- IA-Busca (F-022, etapa 3) ----------------------------------------------
# Busca em linguagem natural/semântica → mapeia p/ produtos do catálogo + "você quis dizer".
# Passa pelo controle de custo. Honra os toggles (interpretação errada/vazia, custo, latência).

@app.post("/api/search/semantic")
def search_semantic(req: SemanticSearchRequest, x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="search", session_id=x_vega_session):
        return ai_features.semantic_search(req.query)


# --- Compare 2 produtos (F-029) ---------------------------------------------
# Orquestração SIMPLES: Compare Coordinator (agente) busca os 2 produtos via tool real (get_price)
# e delega ao Comparator (agente) o veredito exibido. Honra cache/custo (o comparator passa pela
# camada de custo F-022) + toggles.

@app.post("/api/compare")
async def compare_products(req: CompareRequest, authorization: str | None = Header(default=None),
                           x_vega_session: str | None = Header(default=None)):
    user_id = _optional_user_id(authorization)
    with ai_request_scope(feature="compare", session_id=x_vega_session, user_id=user_id) as config:
        result = await compare.arun_compare(req.sku_a, req.sku_b, config=config)
    if result is None:
        raise HTTPException(status_code=404, detail="product not found")
    return result


# --- IA-Home (F-023) --------------------------------------------------------
# Picks personalizados na home "default" (recomendações geradas + blurb). Passa pelo controle
# de custo (F-022). Honra os toggles; `favorites` (skus) enviesa os picks. Standalone (stub) →
# fallback determinístico.

@app.post("/api/home/picks")
def home_picks(req: HomePicksRequest, x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="home_picks", session_id=x_vega_session):
        return ai_features.home_picks(req.favorites)


# --- IA-Carrinho (F-023) ----------------------------------------------------
# Cross-sell/bundle ("complete sua compra") a partir do carrinho atual. Passa pelo controle de
# custo (F-022). Honra os toggles.

@app.post("/api/cart/crosssell")
def cart_crosssell(req: CartCrossSellRequest, x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="cart_crosssell", session_id=x_vega_session):
        return ai_features.cart_crosssell(req.skus)


# --- Auth de demo (F-008, ADR-011) ------------------------------------------
# Sessão por bearer token em `Authorization` (sem cookie — CORS é "*"). Token→user
# vive em memória (DT-010). Helpers leem o header opcional e resolvem o usuário.

def _token_from_header(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _optional_user_id(authorization: str | None) -> str | None:
    """user_id da sessão se houver token válido; None caso contrário (convidado)."""
    token = _token_from_header(authorization)
    return users.session_user_id(token) if token else None


def _require_owner(authorization: str | None) -> str:
    """Gate dos endpoints de config de LLM (F-020): exige sessão de um usuário OWNER.
    401 sem sessão válida; 403 se logado mas sem papel OWNER. Retorna o user_id do owner."""
    user_id = _optional_user_id(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    if not users.is_owner(user_id):
        raise HTTPException(status_code=403, detail="owner only")
    return user_id


def _me_payload(user_id: str) -> dict:
    """Usuário público + tier recomputado pelo gasto; materializa o tier na coluna."""
    user = users.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid session")
    spend = orders.spend_for_user(user_id)
    payload = users.public_user(user, spend)
    if payload["tier"] != user["tier"]:
        users.update_tier(user_id, payload["tier"])  # lazy materialization (espelha ADR-008)
    return payload


@app.post("/api/auth/register")
def register(req: RegisterRequest):
    try:
        user = users.register(req.name, req.email, req.password)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    token = users.create_session(user["id"])
    return {"token": token, "user": _me_payload(user["id"])}


@app.post("/api/auth/login")
def login(req: LoginRequest):
    user = users.authenticate(req.email, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    token = users.create_session(user["id"])
    return {"token": token, "user": _me_payload(user["id"])}


@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    token = _token_from_header(authorization)
    if token:
        users.drop_session(token)
    return {"ok": True}


@app.get("/api/auth/me")
def me(authorization: str | None = Header(default=None)):
    user_id = _optional_user_id(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return {"user": _me_payload(user_id)}


@app.put("/api/auth/me")
def update_me(req: UpdateMeRequest, authorization: str | None = Header(default=None)):
    # Salva/edita o endereço do perfil (F-011); pré-preenche o checkout. Exige sessão.
    user_id = _optional_user_id(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    users.update_address(user_id, req.address)
    return {"user": _me_payload(user_id)}


@app.post("/api/orders")
async def create_order(req: CreateOrderRequest, authorization: str | None = Header(default=None),
                       x_vega_session: str | None = Header(default=None)):
    # Liga ao usuário da sessão se logado (F-008); convidado segue com user_id=None.
    # O fechamento (pipeline/estoque/gateway → PAID/FAILED) vive em checkout.aplace_order
    # (extraído na F-017 p/ o simulador reusar o MESMO caminho).
    user_id = _optional_user_id(authorization)
    with ai_request_scope(feature="fulfillment", session_id=x_vega_session, user_id=user_id) as config:
        return await checkout.aplace_order(
            [i.model_dump() for i in req.items], req.customer.model_dump(), user_id, config=config
        )

@app.get("/api/orders")
def list_orders(authorization: str | None = Header(default=None)):
    # Histórico do usuário logado (F-008): só os próprios pedidos. Exige sessão.
    user_id = _optional_user_id(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return orders.list_orders_for_user(user_id)

@app.get("/api/orders/{order_id}")
def get_order(order_id: str, authorization: str | None = Header(default=None)):
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    # F-019: com sessão (Loja/Conta), o usuário só vê a PRÓPRIA ordem — 404 p/ não vazar
    # existência de pedido alheio. Sem token segue público (Admin/convidado — mesma régua
    # dos controles de workshop, VM por participante).
    user_id = _optional_user_id(authorization)
    if user_id is not None and orders.order_owner(order_id) != user_id:
        raise HTTPException(status_code=404, detail="order not found")
    return order


# --- IA-Pedido (F-024) ------------------------------------------------------
# Resumo de status em linguagem natural (confirmação + detalhe do histórico). Passa pelo controle
# de custo (F-022). Contexto enxuto = dados da própria ordem. Honra os toggles. Resolve a ordem no
# backend (grounding real) com a MESMA régua de autorização do GET /api/orders/{id} (F-019).

@app.post("/api/orders/{order_id}/summary")
def order_summary(order_id: str, authorization: str | None = Header(default=None),
                  x_vega_session: str | None = Header(default=None)):
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    user_id = _optional_user_id(authorization)
    if user_id is not None and orders.order_owner(order_id) != user_id:
        raise HTTPException(status_code=404, detail="order not found")
    with ai_request_scope(feature="order_status", session_id=x_vega_session, user_id=user_id,
                          metadata={"order_id": order_id}):
        return ai_features.order_status_summary(order)


# --- IA-Notificação (F-031) -------------------------------------------------
# Copy gerada de e-mail p/ o evento atual do pedido (confirmação/enviado) — reaproveita a
# notificação simulada (F-005). Exibida como "notification preview" na confirmação do checkout
# e no detalhe do pedido. Passa pelo controle de custo (F-022). Mesma autorização do
# GET /api/orders/{id}.

@app.post("/api/orders/{order_id}/notification")
def order_notification(order_id: str, authorization: str | None = Header(default=None),
                       x_vega_session: str | None = Header(default=None)):
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    user_id = _optional_user_id(authorization)
    if user_id is not None and orders.order_owner(order_id) != user_id:
        raise HTTPException(status_code=404, detail="order not found")
    with ai_request_scope(feature="notification_copy", session_id=x_vega_session, user_id=user_id,
                          metadata={"order_id": order_id}):
        return ai_features.notification_copy(order)


# --- IA-Checkout (F-024) ----------------------------------------------------
# Mensagem de presente (a partir de um breve input) + explicação amigável de bloqueio de
# fraude quando o pedido é barrado. Passam pelo controle de custo (F-022). Honram os toggles.

@app.post("/api/checkout/gift-message")
def checkout_gift_message(req: GiftMessageRequest, x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="gift_message", session_id=x_vega_session):
        return ai_features.gift_message(req.brief)

@app.post("/api/orders/{order_id}/refund")
async def order_refund(order_id: str, authorization: str | None = Header(default=None),
                       x_vega_session: str | None = Header(default=None)):
    # Returns/Refund Coordinator (F-029): cadeia profunda agente→agente→tool a partir de um pedido
    # DELIVERED → marca REFUNDED quando aprovado. Mesma autorização do GET /api/orders/{id} (F-019).
    # 409 se o pedido não é DELIVERED.
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    user_id = _optional_user_id(authorization)
    if user_id is not None and orders.order_owner(order_id) != user_id:
        raise HTTPException(status_code=404, detail="order not found")
    if order["status"] != "DELIVERED":
        raise HTTPException(status_code=409, detail="only delivered orders can be refunded")
    with ai_request_scope(feature="returns", session_id=x_vega_session, user_id=user_id,
                          metadata={"order_id": order_id}) as config:
        return await returns.arun_refund(order, config=config)


# --- IA-Conta (F-031) -------------------------------------------------------
# Insights do histórico + benefícios do tier + recompra a partir dos dados REAIS do usuário
# logado. Passa pelo controle de custo (F-022). Contexto enxuto = resumo dos próprios
# pedidos/tier. Exige sessão.

@app.get("/api/account/insights")
def account_insights(authorization: str | None = Header(default=None),
                     x_vega_session: str | None = Header(default=None)):
    user_id = _optional_user_id(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    user = _me_payload(user_id)  # tier/gasto recomputados (materialização lazy)
    user_orders = orders.list_orders_for_user(user_id)
    with ai_request_scope(feature="account_insights", session_id=x_vega_session, user_id=user_id):
        return ai_features.account_insights(user, user_orders)


@app.post("/api/orders/{order_id}/fraud-explain")
def order_fraud_explain(order_id: str, authorization: str | None = Header(default=None),
                        x_vega_session: str | None = Header(default=None)):
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    user_id = _optional_user_id(authorization)
    if user_id is not None and orders.order_owner(order_id) != user_id:
        raise HTTPException(status_code=404, detail="order not found")
    with ai_request_scope(feature="fraud_explain", session_id=x_vega_session, user_id=user_id,
                          metadata={"order_id": order_id}):
        return ai_features.fraud_explain(order)


# --- Admin (camada de NEGÓCIO — dono; F-014) --------------------------------
# Endpoints aditivos de agregação/admin (não mudam o contrato existente). Vê TODOS
# os pedidos (diferente do GET /api/orders, escopado pela sessão). Não exigem auth —
# consistente com os controles de workshop (/api/problems), numa VM por participante.
# O detalhe da ordem reusa GET /api/orders/{id} (público).

@app.get("/api/admin/summary")
def admin_summary():
    return orders.sales_summary()

# IA-Admin (F-024): insights de vendas + anomalias + reposição a partir de dados AGREGADOS
# (não dumps crus → custo controlado por cache/max_tokens). Passa pelo controle de custo (F-022).
# Honra os toggles. Mesma régua dos demais /api/admin/* (sem auth — controles de workshop, VM
# por participante).
@app.get("/api/admin/insights")
def admin_insights(x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="admin_insights", session_id=x_vega_session):
        return ai_features.admin_insights()

@app.get("/api/admin/orders")
def admin_orders():
    return orders.list_orders()  # todos os pedidos, mais recentes primeiro

@app.get("/api/admin/products")
def admin_products():
    from .tools import CATALOG
    return [{"sku": p["sku"], "name": p["name"], "price": p["price"],
             "stock": p["stock"], "tags": p["tags"], "deleted": bool(p.get("deleted"))}
            for p in CATALOG]

@app.post("/api/admin/seed")
def admin_seed():
    return {"seeded": admin.seed_sample_orders()}  # popula pedidos de exemplo (demo)

@app.delete("/api/admin/orders")
def admin_clear():
    # Clear Sales (F-027/F-GALILEO-7): apaga pedidos, repõe estoque e soft-deletes do catálogo.
    from .tools import reset_stock, restore_catalog
    cleared = orders.clear_all()
    return {
        "cleared": cleared,
        "stock_restored": reset_stock(),
        "catalog_restored": restore_catalog(),
    }

# --- Config de LLM (OWNER-only — F-020, ADR-015) ----------------------------
# Gerencia os provedores da cascata (ordem/enable/modelo/chave). Diferente do resto do
# Admin (sem auth, controles de workshop), estes endpoints são GATED a OWNER: a config
# guarda SEGREDOS (chaves). A API só devolve a versão MASCARADA (sem `api_key`).

@app.get("/api/admin/config/llm-types")
def config_llm_types(authorization: str | None = Header(default=None)):
    # Catálogo de Type presets (base_url + modelos econômicos sugeridos) p/ a UI de conexão
    # (F-021). Não guarda segredo — gated a OWNER só p/ consistência do namespace de config.
    _require_owner(authorization)
    return llm.list_type_presets()

@app.get("/api/admin/config/providers")
def config_list(authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return llm_config.list_providers()

@app.post("/api/admin/config/providers")
def config_create(p: ProviderIn, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return llm_config.create_provider(p.name, p.kind, p.base_url, p.model, p.api_key, p.enabled)

@app.put("/api/admin/config/providers/{provider_id}")
def config_update(provider_id: str, p: ProviderUpdate, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    updated = llm_config.update_provider(provider_id, **p.model_dump(exclude_none=True))
    if updated is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return updated

@app.delete("/api/admin/config/providers/{provider_id}")
def config_delete(provider_id: str, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    if not llm_config.delete_provider(provider_id):
        raise HTTPException(status_code=404, detail="provider not found")
    return {"deleted": provider_id}

@app.post("/api/admin/config/providers/reorder")
def config_reorder(body: ReorderIn, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return llm_config.reorder(body.ids)

@app.post("/api/admin/config/providers/{provider_id}/test")
def config_test(provider_id: str, body: TestProviderIn, authorization: str | None = Header(default=None)):
    # Faz UMA chamada de teste e devolve ok/erro/latência (sem vazar a chave). Usa o provider
    # salvo (com a chave guardada) mesclado com os campos editados na UI; se a UI mandar uma
    # nova chave usa-a, senão mantém a salva — assim o owner testa edições antes de salvar.
    _require_owner(authorization)
    stored = llm_config.get_provider_with_key(provider_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="provider not found")
    edits = body.model_dump(exclude_none=True)
    cfg = {**stored, **{k: v for k, v in edits.items() if k != "api_key" or v}}
    return llm.test_provider(cfg)

# --- Config por agente (OWNER-only — F-021; + features de loja F-022) --------
# Os 6 agentes do Concierge + as features de IA da Loja (product_qa/product_desc/search),
# cada um com connection/model/role/system_prompt. Sem segredo (vai cru ao front), mas gated
# a OWNER p/ consistência do namespace de config.

@app.get("/api/admin/agents/topology")
def config_agents_topology(authorization: str | None = Header(default=None)):
    # Editor visual (F-027): topologia da orquestração (clusters + standalone) derivada do
    # grafo real (agents.py, ADR-018). Owner-only — clicar num agente abre/edita a config (F-021).
    _require_owner(authorization)
    return topology.build()

@app.get("/api/admin/config/agents")
def config_agents(authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return agent_config.list_agents()

@app.put("/api/admin/config/agents/{name}")
def config_agent_update(name: str, p: AgentUpdate, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    updated = agent_config.update_agent(name, **p.model_dump(exclude_none=True))
    if updated is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return updated

@app.post("/api/admin/config/agents/{name}/test")
def config_agent_test(name: str, p: AgentTestIn, authorization: str | None = Header(default=None)):
    # Resolve o LLM do agente (saved + edições da UI) e faz UMA chamada real com o system
    # efetivo (role + system_prompt). Mostra provider/modelo/tokens reais (stub se cair).
    _require_owner(authorization)
    if name not in agent_config.AGENT_NAMES:
        raise HTTPException(status_code=404, detail="agent not found")
    cfg = {**agent_config.get_agent(name), **p.model_dump(exclude_none=True)}
    return llm.test_agent(cfg.get("connection", ""), cfg.get("model", ""),
                          agent_config.effective_system(cfg))

# --- Fonte de config: local | remote (hub/peer — F-026, ADR-019) ------------
# O owner escolhe se a loja é independente (local) ou cliente de um hub (remote, puxa a
# config de outra loja). Owner-only (guarda tokens de enrollment — segredos). A API devolve
# o status SEM segredos (tokens viram flags has_*). Mudar a fonte aplica a quente.

@app.get("/api/admin/config/source")
def config_source_get(authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return hub.settings_public()

@app.put("/api/admin/config/source")
def config_source_set(body: HubSourceIn, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    hub_settings.update_settings(**body.model_dump(exclude_none=True))
    hub.apply_source()  # reinstala a ConfigSource ativa conforme a nova escolha (a quente)
    return hub.settings_public()

@app.post("/api/admin/config/source/sync")
def config_source_sync(authorization: str | None = Header(default=None)):
    # Botão "sync agora": força um pull do hub (só em modo remote).
    _require_owner(authorization)
    return hub.sync_now()

# --- Feature flags de menu/superfícies (F-033) ------------------------------
# O owner liga/desliga áreas do menu (o que os PARTICIPANTES veem). Servidas pela mesma fonte
# de config (local/hub): em `remote` valem as flags do hub (propaga p/ as 150 VMs). A leitura
# das EFETIVAS é PÚBLICA (o front decide o que mostrar/bloquear); a edição é OWNER-only.

@app.get("/api/flags")
def flags_effective():
    # Flags efetivas (públicas o suficiente p/ o front decidir menu/rotas). Sem segredo.
    return feature_flags.effective_flags()

@app.get("/api/admin/flags")
def flags_admin(authorization: str | None = Header(default=None)):
    # Tela de toggles do owner: as flags LOCAIS (editáveis) + as EFETIVAS + a fonte, p/ deixar
    # claro quando o hub está sobrepondo o local (modo remote).
    _require_owner(authorization)
    s = hub_settings.get_settings()
    return {"local": feature_flags.get_local_flags(),
            "effective": feature_flags.effective_flags(),
            "source": s["source"]}

@app.put("/api/admin/flags")
def flags_set(body: FlagsIn, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    feature_flags.update_flags(**body.model_dump(exclude_none=True))
    s = hub_settings.get_settings()
    return {"local": feature_flags.get_local_flags(),
            "effective": feature_flags.effective_flags(),
            "source": s["source"]}

# --- Splunk RUM (Browser Agent) — snippet configurável pelo owner (F-040-RUM) -
# O owner cola o snippet bruto do RUM + liga o toggle; o front injeta no <head> (server-render)
# p/ todas as sessões de navegador. Leitura PÚBLICA (o token RUM é client-side por natureza, vai
# ao HTML de todo visitante); EDIÇÃO owner-only (snippet bruto = JS arbitrário nos clientes — DT).

@app.get("/api/rum")
def rum_public():
    # O que o front injeta (server-render no layout): só traz o snippet quando enabled. Sem gate.
    return rum.public_config()

@app.get("/api/admin/rum")
def rum_admin(authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return rum.get_config()

@app.put("/api/admin/rum")
def rum_set(body: RumIn, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return rum.update_config(**body.model_dump(exclude_none=True))

# --- Lado HUB: servir config a clientes (token-gated; F-026) -----------------
# Endpoint MÁQUINA-A-MÁQUINA (NÃO owner-gated — clientes não têm sessão de owner):
# autentica pelo TOKEN DE ENROLLMENT (`serve_token`), rastreia o cliente e entrega a config
# da cascata. ATENÇÃO: o payload inclui as CHAVES de LLM (DT-013 — chaves trafegam na rede);
# exigir token + HTTPS no lab. Anti-loop pela cadeia `X-Hub-Chain`.

@app.get("/api/hub/config")
def hub_serve(request: Request,
              authorization: str | None = Header(default=None),
              x_hub_chain: str | None = Header(default=None),
              x_hub_env: str | None = Header(default=None),
              user_agent: str | None = Header(default=None)):
    token = _token_from_header(authorization)
    ip = request.client.host if request.client else None
    try:
        return hub.serve_config(token, x_hub_chain, x_hub_env, ip, user_agent)
    except hub.HubError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)

@app.get("/api/admin/hub/status")
def hub_status(authorization: str | None = Header(default=None)):
    # Tela de status de conexão: modo/alvo/saúde/last-sync + clientes (no hub).
    _require_owner(authorization)
    return hub.status()

# --- Enrollment push por IP (F-027, ADR-020) --------------------------------
# CLIENTE: endpoint que ACEITA ser enrolado pelo hub (máquina-a-máquina). Gateado por
# ENROLL_TOKEN (segredo do lab, env baked) — NÃO pela sessão de owner. Seta source=remote
# apontando p/ o hub e puxa já. Sem ENROLL_TOKEN → 401 (standalone-first: loja solta não é
# reconfigurável por rede). HUB: endpoint owner-only que empurra o enroll p/ uma lista de IPs.

@app.post("/api/admin/enroll")
def admin_enroll(body: EnrollIn, authorization: str | None = Header(default=None)):
    token = _token_from_header(authorization)
    if not enroll.verify_enroll_token(token):
        raise HTTPException(status_code=401, detail="invalid enroll token")
    return enroll.apply_enroll(body.hub_url, body.enrollment_token, body.pull_interval_s)

@app.post("/api/admin/hub/enroll-push")
def hub_enroll_push(body: EnrollPushIn, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return enroll.push(body.ips, body.hub_url, body.enroll_token,
                       body.enrollment_token, body.pull_interval_s)

# --- LLM Inspector (OWNER-only, desligável — F-023, ADR-017) ----------------
# Captura LOCAL de atividade de LLM (system/user prompt + resposta + modelo/provider/tokens/
# cache/latência) num ring buffer em memória — o conteúdo de prompt fica local. Owner-only
# (guarda conteúdo de prompt); desligável (flag em memória, default ON; vira feature flag de
# verdade na F-025). Some p/ participantes.

@app.get("/api/admin/llm-activity")
def llm_activity_list(authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return llm_activity.snapshot()  # {enabled, max, entries[]}

@app.put("/api/admin/llm-activity/enabled")
def llm_activity_set_enabled(u: InspectorToggle, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return {"enabled": llm_activity.set_enabled(u.enabled)}

@app.delete("/api/admin/llm-activity")
def llm_activity_clear(authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    llm_activity.clear()
    return {"cleared": True}

# --- Simulador avançado (F-018, ADR-014) ------------------------------------
# Engine asyncio de sessões concorrentes (pool de N usuários + N jornadas que navegam
# e sempre compram, loop espera+sorteio). Controles + poll de status p/ a tela própria
# (/admin/simulator). Aditivo; não muda o contrato existente (só adições).

@app.post("/api/simulator/start")
async def simulator_start(req: SimStartRequest):
    cfg = simulator.SimConfig.from_dict(req.model_dump(exclude_none=True))
    if cfg.mode == "browser":  # F-039: Playwright/Chromium são deps opcionais (não na imagem base)
        from . import sim_browser
        ok, reason = sim_browser.available()
        if not ok:
            raise HTTPException(status_code=400, detail=reason)
    return await simulator.ENGINE.start(cfg)

@app.post("/api/simulator/stop")
async def simulator_stop():
    return await simulator.ENGINE.stop()

@app.post("/api/simulator/pause")
async def simulator_pause(req: SimPauseRequest):
    return simulator.ENGINE.pause(req.paused)

@app.get("/api/simulator/status")
def simulator_status():
    return simulator.ENGINE.status_dict()
