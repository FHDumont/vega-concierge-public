"""Simulador avançado de sessões concorrentes (F-018, ADR-014)."""
from fastapi import APIRouter, HTTPException
from .. import simulator
from ..schemas import SimPauseRequest, SimStartRequest

# Sem `prefix`: cada rota carrega o path completo, igualzinho ao que estava em `api.py`.
router = APIRouter()


# --- Simulador avançado (F-018, ADR-014) ------------------------------------
# Engine asyncio de sessões concorrentes (pool de N usuários + N jornadas que navegam
# e sempre compram, loop espera+sorteio). Controles + poll de status p/ a tela própria
# (/admin/simulator). Aditivo; não muda o contrato existente (só adições).

@router.post("/api/simulator/start")
async def simulator_start(req: SimStartRequest):
    cfg = simulator.SimConfig.from_dict(req.model_dump(exclude_none=True))
    if cfg.mode == "browser":  # F-039: Playwright/Chromium são deps opcionais (não na imagem base)
        # import tardio: Playwright é dependência opcional, fora da imagem base (F-039)
        from .. import sim_browser
        ok, reason = sim_browser.available()
        if not ok:
            raise HTTPException(status_code=400, detail=reason)
    return await simulator.ENGINE.start(cfg)


@router.post("/api/simulator/stop")
async def simulator_stop():
    return await simulator.ENGINE.stop()


@router.post("/api/simulator/pause")
async def simulator_pause(req: SimPauseRequest):
    return simulator.ENGINE.pause(req.paused)


@router.get("/api/simulator/status")
def simulator_status():
    return simulator.ENGINE.status_dict()
