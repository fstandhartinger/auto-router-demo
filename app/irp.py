"""Public suggest-only IRP surface using the published router implementation.

The playground's execution path trims messages, caps output, substitutes frontier
routes and translates reasoning dialects. It cannot transparently relay arbitrary
Chat Completions bodies, so it deliberately exposes suggest-only mode (§5).
"""
from types import SimpleNamespace

from auto_router.irp import Problem, endpoints
from .engine import ENGINE
from .limits import Limiter

# Local CPU work has its own finite allowance; no paid classifier calls.
LIMITER = Limiter(per_ip_per_hour=600, per_ip_per_day=4000,
                  per_subnet_per_hour=2000, per_subnet_per_day=12000,
                  global_per_day=200000, daily_budget_usd=0)


def get_router():
    if not ENGINE.ready():
        raise Problem(503, "Model catalog is starting; please retry.", 30)
    # A separate stateless view leaves the existing playground behavior intact.
    return SimpleNamespace(config=ENGINE.config, success=ENGINE.router.success, classifier=None,
                           _turn_request=ENGINE.router._turn_request, _summary=ENGINE.router._summary)


async def guard(request):
    from .main import _ip
    ip = _ip(request)
    key, subnet = LIMITER.client_key(ip), LIMITER.subnet_key(ip)
    verdict = LIMITER.check(key, subnet)
    if not verdict.allowed:
        raise Problem(503, "Routing request limit reached; please retry later.", verdict.retry_after_s)
    LIMITER.record_run(key, subnet)


routes = endpoints(get_router, guard)
