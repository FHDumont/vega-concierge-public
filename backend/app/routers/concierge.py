"""Os dois fluxos agênticos de conversa — `/api/run` (recomendação) e `/api/chat`."""
import logging
from fastapi import APIRouter, Header
from ..runnable_config import ai_request_scope
from ..agents import arun_chat_workflow
from ..agents import arun_workflow
from ..schemas import ChatRequest, RunRequest
from ._common import _optional_user_id

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


@router.post("/api/chat")
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
