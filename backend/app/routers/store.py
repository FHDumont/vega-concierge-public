"""Vitrine e features de IA da Loja — catálogo, políticas, produto, busca, compare, home e carrinho."""
from fastapi import APIRouter, Header, HTTPException
from .. import ai_features
from ..runnable_config import ai_request_scope
from .. import rag
from ..tools import _active_catalog
from ..graphs.compare import arun_compare
from ..schemas import CartCrossSellRequest, CompareRequest, GiftMessageRequest, HomePicksRequest, ProductDescribeRequest, ProductQARequest, SemanticSearchRequest
from ._common import _optional_user_id

# Sem `prefix`: cada rota carrega o path completo, igualzinho ao que estava em `api.py`.
router = APIRouter()


@router.get("/api/catalog")
def catalog():
    return _active_catalog()


@router.get("/api/policies")
def policies():
    return {"policies": rag.load_policy_files()}


# --- IA-Produto (F-022, etapa 2) --------------------------------------------
# Features de IA da página de detalhe: Q&A fundamentado nos dados do produto e descrição gerada
# (cacheada por produto). Cada uma passa pelo controle de custo. Honram os toggles de problema
# (hallucination/cost/latency). Standalone (stub) devolve fallback gracioso.

@router.post("/api/product/qa")
def product_qa(req: ProductQARequest, x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="product_qa", session_id=x_vega_session):
        ans = ai_features.product_qa(req.sku, req.question)
    if ans is None:
        raise HTTPException(status_code=404, detail="product not found")
    return ans


@router.post("/api/product/describe")
def product_describe(req: ProductDescribeRequest, x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="product_desc", session_id=x_vega_session):
        desc = ai_features.product_describe(req.sku)
    if desc is None:
        raise HTTPException(status_code=404, detail="product not found")
    return desc


# --- IA-Busca (F-022, etapa 3) ----------------------------------------------
# Busca em linguagem natural/semântica → mapeia p/ produtos do catálogo + "você quis dizer".
# Passa pelo controle de custo. Honra os toggles (interpretação errada/vazia, custo, latência).

@router.post("/api/search/semantic")
def search_semantic(req: SemanticSearchRequest, x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="search", session_id=x_vega_session):
        return ai_features.semantic_search(req.query)


# --- Compare 2 produtos (F-029) ---------------------------------------------
# Orquestração SIMPLES: Compare Coordinator (agente) busca os 2 produtos via tool real (get_price)
# e delega ao Comparator (agente) o veredito exibido. Honra cache/custo (o comparator passa pela
# camada de custo F-022) + toggles.

@router.post("/api/compare")
async def compare_products(req: CompareRequest, authorization: str | None = Header(default=None),
                           x_vega_session: str | None = Header(default=None)):
    user_id = _optional_user_id(authorization)
    with ai_request_scope(feature="compare", session_id=x_vega_session, user_id=user_id) as config:
        result = await arun_compare(req.sku_a, req.sku_b, config=config)
    if result is None:
        raise HTTPException(status_code=404, detail="product not found")
    return result


# --- IA-Home (F-023) --------------------------------------------------------
# Picks personalizados na home "default" (recomendações geradas + blurb). Passa pelo controle
# de custo (F-022). Honra os toggles; `favorites` (skus) enviesa os picks. Standalone (stub) →
# fallback determinístico.

@router.post("/api/home/picks")
def home_picks(req: HomePicksRequest, x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="home_picks", session_id=x_vega_session):
        return ai_features.home_picks(req.favorites)


# --- IA-Carrinho (F-023) ----------------------------------------------------
# Cross-sell/bundle ("complete sua compra") a partir do carrinho atual. Passa pelo controle de
# custo (F-022). Honra os toggles.

@router.post("/api/cart/crosssell")
def cart_crosssell(req: CartCrossSellRequest, x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="cart_crosssell", session_id=x_vega_session):
        return ai_features.cart_crosssell(req.skus)


# --- IA-Checkout (F-024) ----------------------------------------------------
# Mensagem de presente (a partir de um breve input) + explicação amigável de bloqueio de
# fraude quando o pedido é barrado. Passam pelo controle de custo (F-022). Honram os toggles.

@router.post("/api/checkout/gift-message")
def checkout_gift_message(req: GiftMessageRequest, x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="gift_message", session_id=x_vega_session):
        return ai_features.gift_message(req.brief)
