"""Painel de problemas do workshop — toggles individuais e presets de use case."""
from fastapi import APIRouter, HTTPException
from ..problems import FLAGS
from ..problems import UC_PRESETS
from ..problems import apply_preset
from ..schemas import ProblemUpdate

# Sem `prefix`: cada rota carrega o path completo, igualzinho ao que estava em `api.py`.
router = APIRouter()


@router.get("/api/problems")
def get_problems():
    return FLAGS.to_dict()


@router.put("/api/problems")
def set_problems(p: ProblemUpdate):
    for k, v in p.model_dump(exclude_none=True).items():
        setattr(FLAGS, k, v)
    # Desligou todos os flags do preset ativo → limpa cenário (evita UC fantasma após refresh).
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
