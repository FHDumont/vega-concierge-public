"""Workshop problem panel — individual toggles and use case presets."""
from fastapi import APIRouter, HTTPException
from ..problems import FLAGS
from ..problems import UC_PRESETS
from ..problems import apply_preset
from ..schemas import ProblemUpdate

# No `prefix`: each route carries the full path, just like it was in `api.py`.
router = APIRouter()


@router.get("/api/problems")
def get_problems():
    return FLAGS.to_dict()


@router.put("/api/problems")
def set_problems(p: ProblemUpdate):
    for k, v in p.model_dump(exclude_none=True).items():
        setattr(FLAGS, k, v)
    # Turned off all active preset flags → clears scenario (avoids phantom UC after refresh).
    if FLAGS.active_scenario and FLAGS.active_scenario in UC_PRESETS:
        keys = UC_PRESETS[FLAGS.active_scenario]
        if not any(getattr(FLAGS, name) for name in keys):
            FLAGS.active_scenario = ""
    return FLAGS.to_dict()


@router.post("/api/problems/preset/{preset_id}")
def apply_problem_preset(preset_id: str):
    try:
        return apply_preset(preset_id).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
