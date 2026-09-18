"""The peer-to-peer network as one routing target among the others.

The Bonsai swarm is a set of volunteers running a ternary 27B model on WebGPU
in their browsers. It speaks the same OpenAI-compatible shape as every other
provider, so the router does not need to know anything special about it: it
enters the catalog with a price of zero, a small context window, no prefix
cache and a modest capability, and then competes on those numbers.

What *is* special is that it can simply not be there. Its public stats endpoint
says how many volunteers are online; when none are, the route is taken out of
the catalog for the next decision and the page says so rather than routing a
request into the void.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx

from .providers import Route
from .settings import SETTINGS

MODEL_NAME = "bonsai-2-27b-p2p"


@dataclass
class SwarmStatus:
    configured: bool = False
    online: bool = False
    providers_online: int = 0
    providers_ready: int = 0
    queue_length: int = 0
    tokens_today: int | None = None
    checked_at: float = 0.0
    error: str | None = None
    site_url: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        return {
            "configured": self.configured,
            "online": self.online,
            "providersOnline": self.providers_online,
            "providersReady": self.providers_ready,
            "queueLength": self.queue_length,
            "tokensToday": self.tokens_today,
            "checkedAt": round(self.checked_at, 1) or None,
            "error": self.error,
            "siteUrl": self.site_url or SETTINGS.bonsai_site_url,
            "model": MODEL_NAME,
        }


class SwarmClient:
    """Polls the swarm's public stats with a short TTL, and never raises."""

    def __init__(self, settings=SETTINGS):
        self.settings = settings
        self._status = SwarmStatus(configured=bool(settings.bonsai_enabled),
                                   site_url=settings.bonsai_site_url)
        self._checked = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.settings.bonsai_enabled and self.settings.bonsai_base_url)

    def cached(self) -> SwarmStatus:
        return self._status

    async def status(self, client: httpx.AsyncClient) -> SwarmStatus:
        if not self.enabled:
            self._status = SwarmStatus(configured=False, site_url=self.settings.bonsai_site_url)
            return self._status
        now = time.time()
        if now - self._checked < self.settings.bonsai_poll_seconds and self._status.checked_at:
            return self._status
        self._checked = now
        url = f"{self.settings.bonsai_base_url}/api/stats"
        try:
            resp = await client.get(url, timeout=6.0)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            self._status = SwarmStatus(configured=True, online=False, checked_at=now,
                                       error=type(exc).__name__,
                                       site_url=self.settings.bonsai_site_url)
            return self._status
        ready = int(data.get("providersReady") or 0)
        self._status = SwarmStatus(
            configured=True,
            online=ready > 0,
            providers_online=int(data.get("providersOnline") or 0),
            providers_ready=ready,
            queue_length=int(data.get("queueLength") or 0),
            tokens_today=data.get("tokensToday"),
            checked_at=now,
            site_url=self.settings.bonsai_site_url,
            raw=data if isinstance(data, dict) else {},
        )
        return self._status

    def route(self) -> Route | None:
        if not self.enabled:
            return None
        token = os.environ.get(self.settings.bonsai_token_env)
        return Route(
            model=MODEL_NAME,
            provider="bonsai-swarm",
            base_url=f"{self.settings.bonsai_base_url}/api/v1",
            api_key=token,
            upstream_id=self.settings.bonsai_model,
            extra_headers={},
            request_extra={},
        )


SWARM = SwarmClient()
