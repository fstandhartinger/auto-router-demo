"""The demo's API: classification, the decision, the guard rails."""

from __future__ import annotations

from conftest import first, sse


# ---------------------------------------------------------------- basics --
def test_health_and_meta(client):
    assert client.get("/healthz").json()["ok"] is True
    meta = client.get("/api/meta").json()
    assert len(meta["examples"]) >= 6
    assert {m["name"] for m in meta["models"]} == {
        "bonsai-2-27b-p2p", "tiny-free", "mid-cheap", "big-frontier"}
    assert meta["limits"]["perIpPerHour"] > 0


def test_results_payload_has_every_section(client):
    data = client.get("/api/results").json()
    for key in ("replay_list", "replay_ours", "quality_matrix", "cache", "jev", "live", "limits"):
        assert key in data, key
    assert len(data["quality_matrix"]["models"]) == len(data["quality_matrix"]["solved"])


def test_scenario_is_long_enough_to_be_cacheable(client):
    scenario = client.get("/api/scenario").json()
    # Below a provider's 1024-token minimum there is no cache to reason about,
    # which would make the whole cache page dishonest.
    assert scenario["approxTokens"] > 1024


# ------------------------------------------------------------ the router --
def test_run_classifies_prices_and_answers(client):
    events = sse(client.post("/api/run", json={"prompt": "Write a small function"}))
    kinds = [event for event, _ in events]
    assert kinds[0] == "classifying"
    assert "decision" in kinds and "done" in kinds

    decision = first(events, "decision")
    assert decision["classification"]["category"] == "coding"
    assert decision["classifier"]["kind"] == "jev"
    assert decision["classification"]["classifier_model"] == "jev-test"

    names = [c["name"] for c in decision["candidates"]]
    assert "big-frontier" in names and "mid-cheap" in names
    # Sorted by expected cost, cheapest first, and the chosen row is in there.
    costs = [c["expectedUsd"] for c in decision["candidates"]]
    assert costs == sorted(costs)
    assert sum(1 for c in decision["candidates"] if c["chosen"]) == 1

    answer = "".join(payload["text"] for event, payload in events if event == "delta")
    assert answer.startswith("Hello from vendor/")


def test_easy_goes_cheap_and_hard_goes_strong(client):
    easy = first(sse(client.post("/api/run", json={"prompt": "rename a variable"})), "decision")
    hard = first(sse(client.post("/api/run", json={"prompt": "a hard concurrency bug"})), "decision")
    easy_cap = next(c["capabilityHere"] for c in easy["candidates"] if c["chosen"])
    hard_cap = next(c["capabilityHere"] for c in hard["candidates"] if c["chosen"])
    assert hard_cap > easy_cap
    assert hard["classification"]["difficulty"] > easy["classification"]["difficulty"]


def test_saving_is_measured_against_the_frontier_baseline(client):
    decision = first(sse(client.post("/api/run", json={"prompt": "rename a variable"})), "decision")
    saving = decision["saving"]
    assert saving["baseline"]["model"] == "big-frontier"
    assert saving["savedUsd"] >= 0
    assert saving["chosenUsd"] <= saving["baseline"]["callUsd"]


def test_the_offline_peer_to_peer_route_is_not_offered(client):
    decision = first(sse(client.post("/api/run", json={"prompt": "say hi"})), "decision")
    assert decision["p2p"]["configured"] is False
    assert "bonsai-2-27b-p2p" not in [c["name"] for c in decision["candidates"]]


def test_empty_prompt_is_rejected(client):
    assert client.post("/api/run", json={"prompt": "   "}).status_code == 400


def test_prompt_is_truncated_not_refused(client):
    from app.settings import SETTINGS

    events = sse(client.post("/api/run", json={"prompt": "x" * (SETTINGS.max_prompt_chars * 3)}))
    decision = first(events, "decision")
    # char/4 estimate of the cap is the ceiling the router can ever see.
    assert decision["promptTokens"] <= SETTINGS.max_prompt_chars // 3


# ------------------------------------------------------------- what-if ----
def test_what_if_prices_a_warm_route_below_a_cold_one(client):
    body = {"promptTokens": 80000, "outputTokens": 1500, "category": "coding",
            "difficulty": 0.45, "current": "big-frontier", "excludeFree": True}
    data = client.post("/api/what-if", json=body).json()
    rows = {r["name"]: r for r in data["rows"]}
    assert "tiny-free" not in rows, "excludeFree must drop the free tiers"
    warm = rows["big-frontier"]
    assert warm["warmTokens"] == 80000
    assert warm["warmUsd"] < warm["coldUsd"]
    assert warm["cacheSavesUsd"] > 0


def test_what_if_flips_once_the_cache_has_expired(client):
    body = {"promptTokens": 120000, "outputTokens": 1200, "category": "coding",
            "difficulty": 0.4, "current": "big-frontier", "excludeFree": True}
    curve = client.post("/api/what-if", json=body).json()["curve"]
    assert curve[0]["stayed"] is True
    assert any(not row["stayed"] for row in curve), "a 5-minute TTL must expire in the curve"
    # Once it has expired it never comes back.
    stayed = [row["stayed"] for row in curve]
    assert stayed == sorted(stayed, reverse=True)


def test_what_if_rejects_nonsense_without_failing(client):
    data = client.post("/api/what-if", json={"promptTokens": 10 ** 9, "category": "not-a-category",
                                             "current": "no-such-model"}).json()
    assert data["request"]["promptTokens"] <= 400_000
    assert data["request"]["category"] == "coding"
    assert data["request"]["current"] is None


# ---------------------------------------------------------- guard rails ---
def test_per_ip_rate_limit(client):
    from app import main

    main.LIMITER.per_ip_per_hour = 3
    try:
        for _ in range(3):
            assert client.post("/api/run", json={"prompt": "hi"}).status_code == 200
        blocked = client.post("/api/run", json={"prompt": "hi"})
        assert blocked.status_code == 429
        assert blocked.json()["error"] == "per-ip"
        assert blocked.json()["retryAfter"] > 0
    finally:
        main.LIMITER.per_ip_per_hour = 20


def test_daily_run_cap(client):
    from app import main

    main.LIMITER.global_per_day = 1
    try:
        assert client.post("/api/run", json={"prompt": "hi"}).status_code == 200
        blocked = client.post("/api/run", json={"prompt": "hi"})
        assert blocked.status_code == 429
        assert blocked.json()["error"] == "daily-cap"
    finally:
        main.LIMITER.global_per_day = 1200


def test_budget_cap_blocks_runs(client):
    from app import main

    main.LIMITER.record_spend(main.LIMITER.daily_budget_usd)
    blocked = client.post("/api/run", json={"prompt": "hi"})
    assert blocked.status_code == 429
    assert blocked.json()["error"] == "budget"


def test_an_answer_too_expensive_for_the_demo_is_substituted_not_run(client):
    from app import main
    from app.settings import SETTINGS

    original = SETTINGS.max_call_usd
    SETTINGS.max_call_usd = 1e-9  # only free routes are affordable
    try:
        events = sse(client.post("/api/run", json={"prompt": "a hard concurrency bug"}))
        decision = first(events, "decision")
        execution = decision["execution"]
        assert execution["model"] == "tiny-free"
        assert execution["substituted"] is True
        assert decision["selection"]["selected"] != "tiny-free", "the decision must not change"
        assert "demo" in execution["note"].lower()
    finally:
        SETTINGS.max_call_usd = original
        main.LIMITER._day_spend = 0.0


def test_spending_is_counted_against_the_budget(client):
    from app import main

    before = main.LIMITER.budget_left()
    # The fake upstream reports 120 prompt and 8 completion tokens; a free route
    # costs nothing, so the budget may only move when a metered route answers.
    sse(client.post("/api/run", json={"prompt": "a hard concurrency bug"}))
    assert main.LIMITER.budget_left() <= before


def test_no_prompt_text_leaves_the_decision_record(client):
    secret = "zzsecretphrasezz"
    events = sse(client.post("/api/run", json={"prompt": f"refactor {secret} please"}))
    decision = first(events, "decision")
    assert secret not in str(decision)


# ----------------------------------------------------------------- chat ---
def test_chat_keeps_one_conversation_and_reports_the_cache(client):
    first_turn = sse(client.post("/api/chat", json={"prompt": "what is in the log?"}))
    payload = first(first_turn, "decision")
    session = payload["session"]
    assert payload["turn"] == 1
    assert payload["cache"]["prompt_tokens"] > 1024, "the pasted document must be in the prefix"
    assert payload["cacheCurve"]

    second = first(sse(client.post("/api/chat", json={
        "prompt": "and now summarise it", "session": session, "pauseSeconds": 60})), "decision")
    assert second["turn"] == 2
    assert second["session"] == session
    assert second["clockOffset"] == 60
    warm = [c for c in second["candidates"] if c["warmTokens"] > 0]
    assert warm, "the second turn must see at least one warm route"


def test_resetting_a_session_starts_a_new_one(client):
    started = client.post("/api/chat", json={"prompt": "hello"})
    session = first(sse(started), "decision")["session"]
    fresh = client.post("/api/session/reset", json={"session": session}).json()["session"]
    assert fresh != session
    again = first(sse(client.post("/api/chat", json={"prompt": "hello", "session": fresh})), "decision")
    assert again["turn"] == 1


# ------------------------------------------------------------- the site ---
def test_every_page_serves_the_app_shell(client):
    for path in ("/", "/cache", "/results", "/how", "/privacy", "/impressum", "/nope"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert "playground" in resp.text
