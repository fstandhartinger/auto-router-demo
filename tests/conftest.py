"""Test fixtures.

Nothing in the test suite reaches the network: the catalog is the hermetic one
in ``tests/catalog.test.json``, the classifier is a stub, and the provider
transport is replaced with a fake that streams a fixed answer.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
ROUTES = {
    "providers": {"fake": {"base_url": "https://fake.invalid/v1", "api_key_env": "FAKE_KEY",
                           "cache": "openai"}},
    "routes": {
        "tiny-free": {"provider": "fake", "upstream_id": "vendor/tiny"},
        "mid-cheap": {"provider": "fake", "upstream_id": "vendor/mid"},
        "big-frontier": {"provider": "fake", "upstream_id": "vendor/big"},
    },
}

os.environ["AUTO_ROUTER_BENCH_OFFLINE"] = "1"
os.environ["AUTO_ROUTER_CACHE_DIR"] = str(TESTS / ".bench-cache")
os.environ["DEMO_CATALOG_FILE"] = str(TESTS / "catalog.test.json")
os.environ["DEMO_ROUTES_JSON"] = json.dumps(ROUTES)
os.environ["DEMO_SPEED_FILE"] = str(TESTS / "speed.test.json")
# The host's health file must never be read by the suite: a healthy or sick
# laptop would otherwise change what the tests decide.
os.environ["DEMO_HEALTH_FILE"] = str(TESTS / "health.test.json")
os.environ["FAKE_KEY"] = "not-a-real-key"
# The counters must never be written to (or read from) a real deployment path.
os.environ["DEMO_STATE_FILE"] = str(TESTS / ".limits.test.json")
os.environ["DEMO_TEST_KEY"] = "test-key"
os.environ.pop("BONSAI_BASE_URL", None)
os.environ.pop("TYPESAFE_API_KEY", None)


#: What the fake transport was asked to send, most recent run last.
SENT: list[dict] = []


@pytest.fixture()
def client(monkeypatch):
    """A TestClient with a stub classifier and a fake upstream."""
    from fastapi.testclient import TestClient

    from app import classify, main, providers

    async def fake_classify(request, context, http_client):
        from auto_router import jev

        hard = "hard" in request.lower()
        return jev.Classification(
            category="coding", category_probs={"coding": 0.9, "general": 0.1},
            category_confidence=0.9, difficulty=0.9 if hard else 0.3,
            difficulty_confidence=0.8, needs_tools=0.1, needs_vision=0.0,
            needs_long_context=0.0, follow_up=0.1, stakes=0.5, latency_s=0.12,
            model="jev-test")

    async def fake_stream(route, messages, max_tokens, http_client, extra=None, timeout=None):
        # Record what the reasoning plan asked this route for, so a test can
        # assert on the body instead of on the log.
        SENT.append({"route": route.model, "extra": dict(extra or {})})
        yield "delta", "Hello from "
        yield "delta", route.upstream_id
        yield "usage", providers.Usage(prompt_tokens=4000, completion_tokens=8, cached_tokens=0)

    monkeypatch.setattr(classify, "classify", fake_classify)
    monkeypatch.setattr(providers, "stream_answer", fake_stream)

    main.LIMITER._hits.clear()
    main.LIMITER._day_runs = 0
    main.LIMITER._day_spend = 0.0
    main.LIMITER.per_ip_per_hour = 10
    main.LIMITER.per_ip_per_day = 30
    main.LIMITER.per_subnet_per_hour = 30
    main.LIMITER.per_subnet_per_day = 100
    main.LIMITER.global_per_day = 1200
    main.LIMITER.daily_budget_usd = 4.0
    main.SESSIONS.clear()
    SENT.clear()
    from app import latency
    latency.BOOK._live.clear()
    latency.BOOK._strikes.clear()
    with TestClient(main.app) as test_client:
        # The catalog is built in the background so a slow benchmark API cannot
        # stop the server from booting; the tests wait for it deliberately.
        from app.engine import ENGINE

        for _ in range(200):
            if ENGINE.ready():
                break
            time.sleep(0.01)
        assert ENGINE.ready(), "the catalog never finished building"
        yield test_client


def sse(response) -> list[tuple[str, dict]]:
    """Parse a server-sent-event body into ``(event, data)`` pairs."""
    out = []
    for block in response.text.split("\n\n"):
        event, data = None, []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if event and data:
            out.append((event, json.loads("\n".join(data))))
    return out


def first(events, name):
    return next(payload for event, payload in events if event == name)
