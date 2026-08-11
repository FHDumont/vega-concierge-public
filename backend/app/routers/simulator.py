"""Advanced concurrent session simulator (F-018, ADR-014)."""
from fastapi import APIRouter, HTTPException
from ..sim import simulator
from ..schemas import SimPauseRequest, SimStartRequest

# No `prefix`: each route carries the full path, just like it was in `api.py`.
router = APIRouter()


# --- Advanced simulator (F-018, ADR-014) ------------------------------------
# Asyncio engine for concurrent sessions (pool of N users + N journeys that browse
# and always buy, wait+draw loop). Controls + status poll for dedicated screen
# (/admin/simulator). Additive; doesn't change existing contract (additions only).

@router.post("/api/simulator/start")
async def simulator_start(req: SimStartRequest):
    cfg = simulator.SimConfig.from_dict(req.model_dump(exclude_none=True))
    if cfg.mode == "browser":  # F-039: Playwright/Chromium are optional deps (not in base image)
        # late import: Playwright is optional dependency, outside base image (F-039)
        from ..sim import sim_browser
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
