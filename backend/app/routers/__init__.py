"""Routers by domain (F-BACKEND-1).

No router uses `prefix`: the full path is written in each route, as it was in
`api.py`. `ROUTERS` sets the registration order on the app.

This order is NOT the same as the routes appeared in the single module — grouping by domain
reorders them, because the domains appeared interleaved there. The resolution is identical anyway:
no literal path is shadowed by a `{param}` from another domain, and `test_api_contract.py`
guards the inventory. Whoever adds a route that depends on precedence should check this.
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
