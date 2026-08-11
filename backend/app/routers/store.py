"""Store storefront and AI features — catalog, policies, product, compare, and cart."""
from fastapi import APIRouter, Header, HTTPException
from ..ai_agents.gift_recommend import recommend_gift
from ..ai_agents.product_qa import answer_product_question
from ..problems import FLAGS
from ..ai_agents.store_compare import arun_compare
from ..ai_agents.store_discovery import cart_crosssell as suggest_cart_crosssell
from ..ai_agents import rag
from ..runnable_config import ai_request_scope
from ..store.tools import _active_catalog
from ..schemas import CartCrossSellRequest, CompareRequest, ProductQARequest
from ._common import _optional_user_id, is_gift_recommend_demo_question

router = APIRouter()


@router.get("/api/catalog")
def catalog():
    return _active_catalog()


@router.get("/api/policies")
def policies():
    return {"policies": rag.load_policy_files()}


@router.post("/api/product/qa")
def product_qa(req: ProductQARequest, x_vega_session: str | None = Header(default=None)):
    question = (req.question or "").strip()
    if FLAGS.cost_spike and is_gift_recommend_demo_question(question):
        with ai_request_scope(feature="gift_recommend", session_id=x_vega_session) as config:
            result = recommend_gift(question, config=config)
        return {"answer": result["answer"], "grounded": True, "layout": None}
    with ai_request_scope(feature="product_qa", session_id=x_vega_session) as config:
        ans = answer_product_question(req.sku, req.question, config=config)
    if ans is None:
        raise HTTPException(status_code=404, detail="product not found")
    return ans


@router.post("/api/compare")
async def compare_products(req: CompareRequest, authorization: str | None = Header(default=None),
                           x_vega_session: str | None = Header(default=None)):
    user_id = _optional_user_id(authorization)
    with ai_request_scope(feature="compare", session_id=x_vega_session, user_id=user_id) as config:
        result = await arun_compare(req.sku_a, req.sku_b, config=config)
    if result is None:
        raise HTTPException(status_code=404, detail="product not found")
    return result


@router.post("/api/cart/crosssell")
def cart_crosssell(req: CartCrossSellRequest, x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="cart_crosssell", session_id=x_vega_session) as config:
        return suggest_cart_crosssell(req.skus, config=config)
