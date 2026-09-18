"""Test fixtures.

Nothing in the test suite reaches the network: the catalog is the hermetic one
in ``tests/catalog.test.json``, the classifier is a stub, and the provider
transport is replaced with a fake that streams a fixed answer.
"""

from __future__ import annotations

import json
import os
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
os.environ["FAKE_KEY"] = "not-a-real-key"
os.environ.pop("BONSAI_BASE_URL", None)
os.environ.pop("TYPESAFE_API_KEY", None)


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

    async def fake_stream(route, messages, max_tokens, http_client):
        yield "delta", "Hello from "
        yield "delta", route.upstream_id
        yield "usage", providers.Usage(prompt_tokens=4000, completion_tokens=8, cached_tokens=0)

    monkeypatch.setattr(classify, "classify", fake_classify)
    monkeypatch.setattr(providers, "stream_answer", fake_stream)

    main.LIMITER._hits.clear()
    main.LIMITER._day_runs = 0
    main.LIMITER._day_spend = 0.0
    main.SESSIONS.clear()
    with TestClient(main.app) as test_client:
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
