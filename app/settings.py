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

    #: Playground runs per address per hour and per day, per /24/-/64 network,
    #: and for the whole demo per day.
    per_ip_per_hour: int = 10
    per_ip_per_day: int = 30
    per_subnet_per_hour: int = 30
    per_subnet_per_day: int = 100
    global_per_day: int = 1200
    #: Dollars of metered provider spend the demo may use per day. Reaching it
    #: pauses paid routes for the rest of the day; free routes keep answering.
    daily_budget_usd: float = 4.0
    #: Where the counters survive a restart. A container without a volume gets
    #: a path inside the container, which still survives a crash-restart.
    state_file: str = "/srv/state/limits.json"
    #: When set, a request may carry ``X-Demo-Test-Key``/``X-Demo-Test-IP`` to
    #: exercise the caps from one machine without pretending to be a visitor.
    test_key: str = ""
    #: A single answer may not be estimated to cost more than this, which is
    #: what keeps the demo on free and cheap routes without having to hard-code
    #: a list of "allowed" models.
    max_call_usd: float = 0.05
    max_prompt_chars: int = 4000
    max_output_tokens: int = 900
    #: Extra tokens a turn may use *on top of* the answer cap when the route is
    #: allowed to think. Several endpoints accept a reasoning effort and then
    #: ignore it, so without headroom a hard question is answered entirely
    #: inside the thinking budget and the visitor gets an empty box. Measured on
    #: 18 Sep 2026: with no headroom, 6 of 14 medium/hard calls produced no
    #: answer at all.
    reasoning_headroom_tokens: int = 900
    #: How long one route may take to show its first token before the demo
    #: moves on to the next candidate. A public playground that sits silent is
    #: indistinguishable from a broken one.
    first_token_deadline_s: float = 12.0
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

    #: Where the separate "a subscription through its own client" write-up lives.
    #: Empty until that exists, and the page then says nothing about it.
    subscription_url: str = ""
    site_url: str = ""
    repo_url: str = "https://github.com/fstandhartinger/auto-model-router"
    demo_repo_url: str = "https://github.com/fstandhartinger/auto-router-demo"
    bench_url: str = "https://benchmarkheaven.com"
    #: Bearer token for the launch-stats endpoints and page. Unset: they 404.
    stats_token: str = ""

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
            per_ip_per_hour=_int("DEMO_RUNS_PER_IP_PER_HOUR", 10),
            per_ip_per_day=_int("DEMO_RUNS_PER_IP_PER_DAY", 30),
            per_subnet_per_hour=_int("DEMO_RUNS_PER_SUBNET_PER_HOUR", 30),
            per_subnet_per_day=_int("DEMO_RUNS_PER_SUBNET_PER_DAY", 100),
            global_per_day=_int("DEMO_RUNS_PER_DAY", 1200),
            daily_budget_usd=_float("DEMO_DAILY_BUDGET_USD", 4.0),
            state_file=os.environ.get("DEMO_STATE_FILE", cls.state_file),
            test_key=os.environ.get("DEMO_TEST_KEY", ""),
            max_call_usd=_float("DEMO_MAX_CALL_USD", 0.05),
            max_prompt_chars=_int("DEMO_MAX_PROMPT_CHARS", 4000),
            max_output_tokens=_int("DEMO_MAX_OUTPUT_TOKENS", 900),
            reasoning_headroom_tokens=_int("DEMO_REASONING_HEADROOM_TOKENS", 900),
            first_token_deadline_s=_float("DEMO_FIRST_TOKEN_DEADLINE_S", 12.0),
            bonsai_enabled=bool(base) and os.environ.get("BONSAI_ENABLED", "1") != "0",
            bonsai_base_url=base,
            bonsai_model=os.environ.get("BONSAI_MODEL") or cls.bonsai_model,
            bonsai_site_url=os.environ.get("BONSAI_SITE_URL") or cls.bonsai_site_url,
            bonsai_poll_seconds=_int("BONSAI_POLL_SECONDS", 30),
            site_url=(os.environ.get("DEMO_SITE_URL") or "").rstrip("/"),
            subscription_url=(os.environ.get("DEMO_SUBSCRIPTION_URL") or "").strip(),
            stats_token=(os.environ.get("DEMO_STATS_TOKEN") or "").strip(),
        )


SETTINGS = Settings.from_env()
