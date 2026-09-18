"""The latency half of the objective.

18 Sep 2026: "What is the capital of Australia?" was routed to a reasoning
model, which spent about fourteen seconds thinking and streamed several hundred
lines of that thinking into the page before a one-line answer. Every candidate
was free, so the money half of the objective was flat and the tie fell to raw
capability.

These tests hold the three things that fixed it: time is part of the score, an
easy request is not allowed to think, and the browser is told that a model is
thinking rather than shown what it thinks.

Nothing here reaches the network.
"""

from __future__ import annotations

import pytest
from conftest import SENT, first, sse

from app import latency


# ------------------------------------------------------- the time estimate --
def test_expected_time_is_shown_next_to_expected_cost(client):
    events = sse(client.post("/api/run", json={"prompt": "Write a small function"}))
    rows = first(events, "decision")["candidates"]
    assert rows, "no candidates priced"
    for row in rows:
        assert row["expectedSeconds"] > 0, row["name"]
        assert row["expectedUsd"] is not None


def test_a_thinking_route_is_expected_to_take_longer_than_a_silent_one(client):
    """Same prompt, same output budget: the difference is the thinking."""
    events = sse(client.post("/api/run", json={"prompt": "This one is hard"}))
    rows = {r["name"]: r for r in first(events, "decision")["candidates"]}
    # mid-cheap is the fixture's slow thinker (2.0s first token, 30 tok/s, 900
    # reasoning tokens on a hard request); tiny-free does not reason at all.
    assert rows["mid-cheap"]["expectedSeconds"] > rows["tiny-free"]["expectedSeconds"]


def test_the_weight_is_exactly_the_seconds_surcharge(client):
    """Price a second at nothing and the score is the published policy's, to the cent.

    Asked of the policy directly: a second /api/run teaches the token estimator
    what a real prompt weighs, which moves the money half and would hide the
    only thing this test is about.
    """
    from auto_router.policies import Conversation, TurnRequest

    from app.engine import ENGINE

    policy = ENGINE.router.policy
    ctx = ENGINE.context()
    conv = Conversation()
    req = TurnRequest(category="coding", difficulty=0.9, prompt_tokens=2000,
                      output_tokens=900, now=0.0)
    pool = [m for m in ctx.catalog.all()]
    model = ctx.catalog["mid-cheap"]

    priced, _ = policy.value(model, conv, req, ctx, req.difficulty, pool)
    money, _ = super(type(policy), policy).value(model, conv, req, ctx, req.difficulty, pool)
    seconds = policy.seconds_for(model, req)

    assert seconds > 0
    assert priced == pytest.approx(money + policy.second_usd * seconds)
    assert priced > money


def test_an_unmeasured_route_is_not_assumed_to_be_fast():
    speed = latency.BOOK.speed("a-route-nobody-has-timed")
    assert speed.source == "default"
    assert speed.ttft_s >= latency.DEFAULT_TTFT_S


def test_the_demos_own_timings_feed_the_next_decision():
    before = latency.BOOK.speed("mid-cheap")
    latency.BOOK.observe("mid-cheap", ttft_s=0.2, decode_tps=500.0, tokens=200)
    after = latency.BOOK.speed("mid-cheap")
    assert after.source == "live"
    assert after.ttft_s < before.ttft_s
    latency.BOOK._live.clear()


# ------------------------------------------------------------- reasoning --
MID = {"speed": {"reasoning": True, "thinking_dialect": "chat_template_kwargs"}}
FRONTIER = {"speed": {"reasoning": True, "thinking_dialect": "reasoning_effort"}}
SILENT = {"speed": {"reasoning": False, "thinking_dialect": None}}
UNCONTROLLED = {"speed": {"reasoning": True, "thinking_dialect": None}}


def test_an_easy_request_is_answered_without_thinking():
    plan = latency.plan_reasoning("mid-cheap", 0.1, MID)
    assert plan.level == "off"
    assert plan.request_extra == {"chat_template_kwargs": {"enable_thinking": False}}


def test_a_hard_request_may_still_think():
    assert latency.plan_reasoning("mid-cheap", 0.95, MID).level == "full"


def test_thinking_is_capped_where_the_provider_takes_a_budget():
    plan = latency.plan_reasoning("big-frontier", 0.5, FRONTIER)
    assert plan.level == "low"
    assert plan.budget_tokens <= latency.MAX_REASONING_TOKENS


def test_a_route_that_never_reasons_is_not_told_to_stop():
    plan = latency.plan_reasoning("tiny-free", 0.1, SILENT)
    assert plan.level == "none" and plan.request_extra == {}


def test_a_reasoning_route_we_cannot_steer_is_still_priced_as_thinking():
    """Otherwise a slow route wins on a fiction: that it never thinks."""
    plan = latency.plan_reasoning("big-frontier", 0.1, UNCONTROLLED)
    assert plan.level == "full"
    assert plan.request_extra == {}


def test_only_measured_dialects_are_ever_sent():
    # An invented dialect must produce no request body rather than a guess.
    plan = latency.plan_reasoning("x", 0.1, {"speed": {"reasoning": True,
                                                       "thinking_dialect": "made-up"}})
    assert plan.request_extra == {}


def test_the_plan_reaches_the_provider_request(client):
    """The easy prompt's 'no thinking' actually leaves the building."""
    sse(client.post("/api/run", json={"prompt": "Write a small function"}))
    assert SENT, "the fake transport was never called"
    sent = SENT[-1]
    if sent["route"] == "mid-cheap":
        assert sent["extra"] == {"chat_template_kwargs": {"enable_thinking": False}}
    else:
        # Whichever route won, it must not have been asked for something the
        # dialect table does not contain.
        assert set(sent["extra"]) <= {"chat_template_kwargs", "reasoning"}


# ------------------------------------------------- what the browser sees --
def test_reasoning_tokens_are_not_streamed_to_the_browser(client, monkeypatch):
    from app import providers

    async def thinker(route, messages, max_tokens, http_client, extra=None, timeout=None):
        for _ in range(200):
            yield "reasoning", "thinking out loud, at length. "
        yield "delta", "42"
        yield "usage", providers.Usage(prompt_tokens=10, completion_tokens=2, cached_tokens=0)

    monkeypatch.setattr(providers, "stream_answer", thinker)
    events = sse(client.post("/api/run", json={"prompt": "Write a small function"}))
    kinds = [event for event, _ in events]
    # Not one of the two hundred reasoning chunks reaches the page...
    assert "reasoning" not in kinds
    # ... but the visitor is told that it happened, and for how long.
    assert kinds.count("thinking") == 1
    done = first(events, "thinking_done")
    assert done["chars"] > 0 and done["seconds"] >= 0


def test_a_route_that_says_nothing_is_replaced(client, monkeypatch):
    import asyncio

    from app import main, providers

    stalled = []

    async def stall(route, messages, max_tokens, http_client, extra=None, timeout=None):
        if not stalled:
            stalled.append(route.model)
            await asyncio.sleep(5)
            yield "delta", "too late"
            return
        yield "delta", "the next route answered"
        yield "usage", providers.Usage(prompt_tokens=10, completion_tokens=4, cached_tokens=0)

    monkeypatch.setattr(providers, "stream_answer", stall)
    monkeypatch.setattr(main, "FIRST_TOKEN_DEADLINE_S", 0.2)
    events = sse(client.post("/api/run", json={"prompt": "Write a small function"}))
    kinds = [event for event, _ in events]
    assert "rerouted" in kinds, kinds
    rerouted = first(events, "rerouted")
    assert rerouted["from"] != rerouted["to"]
    assert "".join(d["text"] for e, d in events if e == "delta") == "the next route answered"


def test_an_unhealthy_route_is_not_offered(client, monkeypatch):
    from app.engine import ENGINE

    monkeypatch.setattr(latency.BOOK, "_health",
                        {"mid-cheap": {"health": "unhealthy"}})
    monkeypatch.setattr(latency.BOOK, "_health_at", float("inf"))
    assert "mid-cheap" in ENGINE.excluded(p2p_online=True)


def test_the_last_route_standing_is_used_even_if_it_looks_sick(client, monkeypatch):
    """A slow answer beats no answer; the demo must not exclude everything."""
    from app.engine import ENGINE

    sick = {m.name: {"health": "unhealthy"} for m in ENGINE.config.catalog.all()}
    monkeypatch.setattr(latency.BOOK, "_health", sick)
    monkeypatch.setattr(latency.BOOK, "_health_at", float("inf"))
    excluded = ENGINE.excluded(p2p_online=True)
    assert len(ENGINE.config.catalog.all()) - len(excluded) >= latency.MIN_CANDIDATES


# ------------------------------------------------- the decision still works --
def test_easy_still_goes_cheap_and_hard_still_escalates(client):
    """Speed must not have bought its way past the quality half of the objective."""
    easy = first(sse(client.post("/api/run", json={"prompt": "Write a small function"})),
                 "decision")
    hard = first(sse(client.post("/api/run", json={"prompt": "This one is hard"})), "decision")
    easy_cap = next(c["capabilityHere"] for c in easy["candidates"] if c["chosen"])
    hard_cap = next(c["capabilityHere"] for c in hard["candidates"] if c["chosen"])
    assert hard_cap >= easy_cap


def test_a_route_the_policy_never_scored_can_still_answer(client, monkeypatch):
    """The fallback queue is not limited to the ranked candidates.

    Introduced and caught on 18 Sep 2026: ranking the fallbacks by the policy's
    own score accidentally narrowed them to the routes the policy had priced,
    so a turn whose chosen route was unaffordable could end with "no route"
    while a perfectly usable one sat in the catalog.
    """
    from auto_router.policies import Conversation

    from app import main
    from app.engine import ENGINE

    seen = []
    monkeypatch.setattr(ENGINE, "candidate_rows", lambda d: seen.append(d) or [])
    events = sse(client.post("/api/run", json={"prompt": "Write a small function"}))

    assert seen, "the decision was never priced"
    offered = [e.model_name for e in main._fallbacks(seen[0], Conversation(), skip=set())]
    assert offered, "no fallback route offered although the catalog has usable ones"
    # And the turn still produced a real answer rather than an apology.
    assert "delta" in [event for event, _ in events]


def test_a_route_that_failed_sits_out_the_next_decision(client, monkeypatch):
    """The health signal that works where the host's prober cannot be read."""
    from app.engine import ENGINE

    assert not latency.BOOK.cooling_off("mid-cheap")
    latency.BOOK.penalise("mid-cheap", "no first token within 12s")
    assert latency.BOOK.cooling_off("mid-cheap")
    assert "mid-cheap" in ENGINE.excluded(p2p_online=True)
    # And it is offered again as soon as it answers.
    latency.BOOK.forgive("mid-cheap")
    assert "mid-cheap" not in ENGINE.excluded(p2p_online=True)


def test_the_cooldown_is_bounded(monkeypatch):
    for _ in range(200):
        latency.BOOK.penalise("mid-cheap", "still failing")
    strikes, at, _ = latency.BOOK._strikes["mid-cheap"]
    monkeypatch.setattr(latency.time, "time", lambda: at + latency.MAX_COOLDOWN_S + 1)
    assert not latency.BOOK.cooling_off("mid-cheap")
    latency.BOOK.forgive("mid-cheap")
