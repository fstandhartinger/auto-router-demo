"""Demo settings, all from the environment.

Nothing here has a secret as a default and nothing secret is ever returned to a
browser. The public repo knows *which* models the router reasons about
(``app/data/catalog.json``); which endpoint serves them, and under which
account, arrives at runtime in ``DEMO_ROUTES_JSON``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DATA = Path(__file__).parent / "data"


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _json(name: str, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


@dataclass
class Settings:
    #: {"providers": {name: {base_url, api_key_env, cache, extra_headers}},
    #:  "routes": {model_name: {provider, upstream_id, request_extra}}}
    routes: dict = field(default_factory=dict)

    #: Playground runs per IP per hour, and for the whole demo per day.
    per_ip_per_hour: int = 20
    global_per_day: int = 1200
    #: Dollars of metered provider spend the demo may use per day.
    daily_budget_usd: float = 3.0
    #: A single answer may not be estimated to cost more than this, which is
    #: what keeps the demo on free and cheap routes without having to hard-code
    #: a list of "allowed" models.
    max_call_usd: float = 0.03
    max_prompt_chars: int = 4000
    max_output_tokens: int = 900
    #: Chat turns kept per browser session, and sessions kept at all.
    max_chat_turns: int = 12
    max_sessions: int = 500

    # Peer-to-peer network (the Bonsai swarm). Off until its endpoint is live.
    bonsai_enabled: bool = False
    bonsai_base_url: str = ""
    bonsai_token_env: str = "BONSAI_API_TOKEN"
    bonsai_model: str = "bonsai-swarm/ternary-bonsai-2-27b"
    bonsai_site_url: str = "https://bonsai-swarm.app.mintapis.com"
    bonsai_poll_seconds: int = 30

    site_url: str = ""
    repo_url: str = "https://github.com/fstandhartinger/auto-model-router"
    demo_repo_url: str = "https://github.com/fstandhartinger/auto-router-demo"
    bench_url: str = "https://benchmarkheaven.com"

    @classmethod
    def from_env(cls) -> "Settings":
        routes = _json("DEMO_ROUTES_JSON", {})
        if not routes:
            path = os.environ.get("DEMO_ROUTES_FILE", "").strip()
            if path and Path(path).expanduser().exists():
                routes = json.loads(Path(path).expanduser().read_text())
        base = (os.environ.get("BONSAI_BASE_URL") or "").rstrip("/")
        return cls(
            routes=routes or {},
            per_ip_per_hour=_int("DEMO_RUNS_PER_IP_PER_HOUR", 20),
            global_per_day=_int("DEMO_RUNS_PER_DAY", 1200),
            daily_budget_usd=_float("DEMO_DAILY_BUDGET_USD", 3.0),
            max_call_usd=_float("DEMO_MAX_CALL_USD", 0.03),
            max_prompt_chars=_int("DEMO_MAX_PROMPT_CHARS", 4000),
            max_output_tokens=_int("DEMO_MAX_OUTPUT_TOKENS", 900),
            bonsai_enabled=bool(base) and os.environ.get("BONSAI_ENABLED", "1") != "0",
            bonsai_base_url=base,
            bonsai_model=os.environ.get("BONSAI_MODEL") or cls.bonsai_model,
            bonsai_site_url=os.environ.get("BONSAI_SITE_URL") or cls.bonsai_site_url,
            bonsai_poll_seconds=_int("BONSAI_POLL_SECONDS", 30),
            site_url=(os.environ.get("DEMO_SITE_URL") or "").rstrip("/"),
        )


SETTINGS = Settings.from_env()
