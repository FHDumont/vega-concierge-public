"""Os dois fluxos agênticos de conversa — `/api/run` (recomendação) e `/api/chat`."""
import logging
from fastapi import APIRouter, Header
from ..ai_agents import security
from ..ai_agents.gift_recommend import recommend_gift
from ..problems import FLAGS
from ..runnable_config import ai_request_scope
from ..ai_agents.chat_workflow import arun_chat_workflow
from ..ai_agents.concierge_workflow import arun_workflow
from ..schemas import ChatRequest, GiftRecommendRequest, RunRequest, SecurityActionRequest
from ._common import _optional_user_id, is_gift_recommend_demo_question

log = logging.getLogger(__name__)

# Sem `prefix`: cada rota carrega o path completo, igualzinho ao que estava em `api.py`.
router = APIRouter()


@router.post("/api/run")
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


@router.post("/api/security/actions")
def security_action(req: SecurityActionRequest, authorization: str | None = Header(default=None),
                    x_vega_session: str | None = Header(default=None)):
    """Execute an explicit privileged action through the isolated SecurityAgent."""
    user_id = _optional_user_id(authorization)
    with ai_request_scope(feature="security", session_id=x_vega_session, user_id=user_id) as config:
        if req.action == "delete_product":
            return security.delete_catalog_product(req.sku, prompt=req.prompt, config=config)
        return {"customers": security.export_recent_customers(sku=req.sku, limit=5, config=config)}


def _last_user_message(messages: list[dict]) -> str:
    for item in reversed(messages):
        if item.get("role") == "user":
            return str(item.get("content") or "").strip()
    return ""


def _gift_to_chat_response(result: dict) -> dict:
    return {
        "answer": result.get("answer"),
        "intent": "recommend",
        "artifacts": {
            "recommended": result.get("recommended"),
            "quality": result.get("quality"),
        },
        "language": "en",
        "llm_unavailable": False,
        "trace": [],
    }


@router.post("/api/recommend/gift")
def recommend_gift_endpoint(req: GiftRecommendRequest, x_vega_session: str | None = Header(default=None)):
    with ai_request_scope(feature="gift_recommend", session_id=x_vega_session) as config:
        return recommend_gift(req.request, config=config)


@router.post("/api/chat")
async def chat(req: ChatRequest, authorization: str | None = Header(default=None),
             x_vega_session: str | None = Header(default=None)):
    user_id = _optional_user_id(authorization)
    error = None
    ctx = req.context.model_dump(exclude_none=True) if req.context else {}
    try:
        messages = [m.model_dump() for m in req.messages]
        last_user = _last_user_message(messages)
        delegate_gift = FLAGS.cost_spike and is_gift_recommend_demo_question(last_user)
        # UC-2: trace root must be `gift_recommend`, not `chat`, so session-level Agent Efficiency
        # reads redundant steps from the workflow I/O instead of a lean chat envelope.
        feature = "gift_recommend" if delegate_gift else "chat"
        with ai_request_scope(feature=feature, session_id=x_vega_session, user_id=user_id) as config:
            if delegate_gift:
                final = _gift_to_chat_response(recommend_gift(last_user, config=config))
            else:
                final = await arun_chat_workflow(messages, context=ctx, config=config)
    except Exception as e:
        log.exception("POST /api/chat failed")
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
