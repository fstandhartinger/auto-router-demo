"""Frontier routes: priced and choosable, never run on the free demo."""

from __future__ import annotations

from conftest import SENT, first, sse


def _selected_for(client, prompt):
    return first(sse(client.post("/api/run", json={"prompt": prompt})), "decision")["selection"]["selected"]


def _as_frontier(monkeypatch, name):
    from app.engine import ENGINE

    meta = dict(ENGINE.catalog_meta[name])
    meta["frontier"] = True
    monkeypatch.setitem(ENGINE.catalog_meta, name, meta)


def test_a_frontier_pick_is_announced_and_answered_by_an_affordable_route(client, monkeypatch):
    prompt = "a hard concurrency bug"
    chosen = _selected_for(client, prompt)
    _as_frontier(monkeypatch, chosen)
    SENT.clear()

    events = sse(client.post("/api/run", json={"prompt": prompt}))
    decision = first(events, "decision")
    # The decision itself is untouched: the frontier route still wins it.
    assert decision["selection"]["selected"] == chosen
    frontier = decision["frontier"]
    assert frontier["model"] == chosen
    assert frontier["callUsd"] is not None and frontier["callUsd"] > 0
    execution = decision["execution"]
    assert execution["model"] != chosen
    assert execution["substituted"] is True
    assert "too expensive to run on this free demo" in execution["note"]
    assert "Run the router yourself" in execution["note"]
    # And nothing was ever sent to the frontier route.
    assert all(sent["route"] != chosen for sent in SENT)
    assert first(events, "done")


def test_a_frontier_route_is_never_a_fallback_or_an_escalation(client, monkeypatch):
    from app import main
    from app.engine import ENGINE

    for m in ENGINE.config.catalog.all():
        if m.name != "tiny-free":
            _as_frontier(monkeypatch, m.name)
    SENT.clear()
    events = sse(client.post("/api/run", json={"prompt": "a hard concurrency bug"}))
    execution = first(events, "decision")["execution"]
    assert execution is None or execution["model"] == "tiny-free"
    assert all(sent["route"] == "tiny-free" for sent in SENT)
    main.LIMITER._day_spend = 0.0


def test_the_catalog_marks_frontier_routes_as_not_runnable(client, monkeypatch):
    from app.engine import ENGINE

    _as_frontier(monkeypatch, "big-frontier")
    ENGINE.catalog_meta["big-frontier"]["executable"] = False
    rows = {r["name"]: r for r in ENGINE.model_rows()}
    assert rows["big-frontier"]["frontier"] is True
    assert rows["big-frontier"]["executable"] is False
    assert rows["mid-cheap"]["frontier"] is False


def test_the_shipped_catalog_carries_the_four_frontier_models():
    import json
    from pathlib import Path

    catalog = json.loads((Path(__file__).parent.parent / "app/data/catalog.json").read_text())
    frontier = {m["name"]: m for m in catalog["models"] if (m.get("demo") or {}).get("frontier")}
    assert set(frontier) == {"claude-fable-5.1", "gpt-6-astra", "claude-opus-5", "gpt-5.6-sol"}
    # Real Benchmark Heaven ids, so prices and scores are read, not typed in.
    assert frontier["claude-fable-5.1"]["bench_id"] == "claude-fable-5.1::medium"
    assert frontier["gpt-6-astra"]["bench_id"] == "gpt-6-astra::medium"
    for m in frontier.values():
        assert "prices" not in m and "capability" not in m
