"""Routers por domínio (F-BACKEND-1).

Nenhum router usa `prefix`: o path completo fica escrito em cada rota, como estava em
`api.py`. `ROUTERS` fixa a ordem de registro na app.

Essa ordem NÃO é a mesma em que as rotas apareciam no módulo único — agrupar por domínio
reordena, porque os domínios apareciam intercalados lá. A resolução é idêntica mesmo assim:
nenhum path literal é sombreado por um `{param}` de outro domínio, e `test_api_contract.py`
guarda o inventário. Quem acrescentar rota que dependa de precedência precisa conferir isto.
"""
from . import (
    health,
    store,
    problems,
    ops,
    concierge,
    auth,
    orders,
    admin,
    admin_config,
    simulator,
)

ROUTERS = (
    health.router,
    store.router,
    problems.router,
    ops.router,
    concierge.router,
    auth.router,
    orders.router,
    admin.router,
    admin_config.router,
    simulator.router,
)
