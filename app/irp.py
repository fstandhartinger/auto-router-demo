"""Public suggest-only IRP surface using the published router implementation.

The playground's execution path trims messages, caps output, substitutes frontier
routes and translates reasoning dialects. It cannot transparently relay arbitrary
Chat Completions bodies, so it deliberately exposes suggest-only mode (§5).

Rankings classify the request with the same local s1-llm-auto-router the playground uses
(no hosted call, no LLM fallback on this path) under a wall-clock budget and a concurrency cap;
when the classifier cannot answer in time the ranking still succeeds on the conservative prior and
says so. The models listed come from the wide IRP catalog (``app/irp_catalog.py``).
"""
import ipaddress
import os
from types import SimpleNamespace

from auto_router import s1_router
from auto_router.irp import BudgetedClassifier, Problem, endpoints

from . import classify
from .engine import ENGINE
from .irp_catalog import CATALOG
from .limits import Limiter


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


# Local CPU work has its own finite allowance; no paid classifier calls.
LIMITER = Limiter(per_ip_per_hour=600, per_ip_per_day=4000,
                  per_subnet_per_hour=2000, per_subnet_per_day=12000,
                  global_per_day=200000, daily_budget_usd=0)


class Trusted:
    """Callers whose traffic arrives from known marketplace addresses, with a bucket of their own.

    ``IRP_TRUSTED_IPS`` is a comma list of addresses or CIDR blocks. AntSeed reaches this service from
    one seller address, so one busy marketplace must not be throttled like an anonymous visitor, and a
    flood of anonymous traffic must not use up the marketplace's allowance (the buckets are separate).
    """

    def __init__(self, spec: str = "", per_hour: int = 20000, per_day: int = 200000):
        self.networks = []
        for part in (spec or "").split(","):
            part = part.strip()
            if part:
                try:
                    self.networks.append(ipaddress.ip_network(part, strict=False))
                except ValueError:
                    continue  # a typo must never widen access
        self.limiter = Limiter(per_ip_per_hour=per_hour, per_ip_per_day=per_day,
                               per_subnet_per_hour=per_hour, per_subnet_per_day=per_day,
                               global_per_day=per_day, daily_budget_usd=0)

    @classmethod
    def from_env(cls) -> "Trusted":
        return cls(os.environ.get("IRP_TRUSTED_IPS", ""), _env_int("IRP_TRUSTED_PER_HOUR", 20000),
                   _env_int("IRP_TRUSTED_PER_DAY", 200000))

    @staticmethod
    def peer(request) -> str:
        """The address the platform proxy saw: the LAST X-Forwarded-For entry, else the socket peer.

        Every proxy in front of the container appends the address it received the connection from, so
        the right-most entry cannot be chosen by the caller. Entries to its left are caller-controlled
        (a public client can send ``X-Forwarded-For: <trusted ip>``), which is why the trusted check
        never looks at them. ``main._ip`` keeps taking the first entry for the public buckets (unchanged
        behaviour); that value is never used to grant the trusted bucket.
        """
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded.strip():
            return forwarded.split(",")[-1].strip()
        return request.client.host if request.client else ""

    def contains(self, ip: str) -> bool:
        try:
            address = ipaddress.ip_address(ip.strip())
        except ValueError:
            return False
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address in net for net in self.networks if net.version == address.version)


TRUSTED = Trusted.from_env()

#: The same s1 instance the playground classifies with, called through ``classify.S1`` so a test double applies.
CLASSIFIER = BudgetedClassifier(
    lambda request, context: classify.S1(request, context),
    f"{s1_router.MODEL_ID}@{s1_router.REVISION[:12]}",
    timeout_s=_env_int("IRP_CLASSIFIER_TIMEOUT_MS", 800) / 1000.0,
    concurrency=_env_int("IRP_CLASSIFIER_CONCURRENCY", 3),
    loaded=lambda: bool(getattr(classify.S1, "loaded", True)))


def _config():
    if not ENGINE.ready():
        raise Problem(503, "Model catalog is starting; please retry.", 30)
    return CATALOG.view(ENGINE.config)


def get_router():
    config = _config()
    # A separate stateless view leaves the existing playground behavior intact.
    return SimpleNamespace(config=config, success=ENGINE.router.success, classifier=CLASSIFIER,
                           _turn_request=ENGINE.router._turn_request, _summary=ENGINE.router._summary)


def models_extra() -> dict:
    try:
        info = CATALOG.info(_config())
    except Exception:  # noqa: BLE001 - status fields are best effort
        info = CATALOG.info()
    return {**info, "classifier": CLASSIFIER.status()}


async def guard(request):
    from .main import _ip
    if TRUSTED.networks and TRUSTED.contains(TRUSTED.peer(request)):
        limiter, ip = TRUSTED.limiter, TRUSTED.peer(request)
    else:
        limiter, ip = LIMITER, _ip(request)
    key, subnet = limiter.client_key(ip), limiter.subnet_key(ip)
    verdict = limiter.check(key, subnet)
    if not verdict.allowed:
        raise Problem(503, "Routing request limit reached; please retry later.", verdict.retry_after_s)
    limiter.record_run(key, subnet)


routes = endpoints(get_router, guard, models_extra)
