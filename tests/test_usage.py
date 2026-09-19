"""Usage counting: aggregate, cookie-free, and behind a token."""

from __future__ import annotations

from conftest import sse

AUTH = {"Authorization": "Bearer stats-test-token"}
BROWSER = {"user-agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/131.0",
           "sec-fetch-dest": "document", "accept": "text/html"}


def test_page_request_classification():
    from app.usage import classify_page_request as c

    assert c("GET", "how", BROWSER) == {"path": "/how", "referrer": "", "visit": True}
    assert c("GET", "", {**BROWSER, "referer": "https://news.ycombinator.com/item?id=1"})["referrer"] == "news.ycombinator.com"
    same = c("GET", "run", {**BROWSER, "referer": "https://whichmodel.app.mintapis.com/"})
    assert same["visit"] is False and same["referrer"] == ""
    assert c("GET", "", {**BROWSER, "user-agent": "Twitterbot/1.0"}) is None
    assert c("GET", "", {**BROWSER, "sec-gpc": "1"}) is None
    assert c("GET", "", {**BROWSER, "dnt": "1"}) is None
    assert c("GET", "", {**BROWSER, "sec-fetch-dest": "script"}) is None
    assert c("GET", "", {**BROWSER, "sec-purpose": "prefetch"}) is None
    assert c("POST", "", BROWSER) is None


def test_the_counters_hold_no_identifier(tmp_path):
    from app.usage import Usage

    u = Usage(str(tmp_path / "u.json"), flush_every_s=0)
    u.page({"path": "/", "referrer": "example.org", "visit": True})
    u.event("runs")
    u.spend(0.0012)
    text = (tmp_path / "u.json").read_text()
    assert "Mozilla" not in text and "127.0.0.1" not in text
    again = Usage(str(tmp_path / "u.json"))
    day = next(iter(again.snapshot()["days"].values()))
    assert day["views"] == 1 and day["runs"] == 1 and day["spendUsd"] == 0.0012
    assert again.boot == u.boot, "a crash-restart keeps its boot, so the collector does not double count"


def test_small_referrers_are_folded_away(tmp_path):
    from app.usage import Usage

    u = Usage(None)
    for _ in range(5):
        u.page({"path": "/", "referrer": "big.example", "visit": True})
    u.page({"path": "/", "referrer": "one-person.example", "visit": True})
    names = [r["name"] for r in u.report()["referrers"]]
    assert "big.example" in names and "one-person.example" not in names and "(other)" in names


def test_runs_installs_and_pages_are_counted(client):
    from app.usage import USAGE, day_of

    before = dict(USAGE.snapshot()["days"].get(day_of(), {}))
    sse(client.post("/api/run", json={"prompt": "hi"}))
    script = client.get("/install.sh")
    assert script.status_code == 200 and "auto-model-router" in script.text
    client.get("/how", headers=BROWSER)
    after = USAGE.snapshot()["days"][day_of()]
    assert after["runs"] == before.get("runs", 0) + 1
    assert after["installs"] == before.get("installs", 0) + 1
    assert after["views"] == before.get("views", 0) + 1


def test_stats_endpoints_need_the_token(client):
    assert client.get("/api/usage").status_code == 404
    assert client.get("/api/usage", headers={"Authorization": "Bearer wrong"}).status_code == 404
    assert client.get("/api/launch-stats").status_code == 404
    assert client.post("/api/launch-stats", json={"x": 1}).status_code == 404
    assert "days" in client.get("/api/usage", headers=AUTH).json()
    assert client.post("/api/launch-stats", json={"generatedAt": "now", "github": {}}, headers=AUTH).json()["ok"]
    data = client.get("/api/launch-stats", headers=AUTH).json()
    assert data["collected"]["generatedAt"] == "now"
    assert "daily" in data["live"]
    # The page itself is public and says nothing until it has the token.
    page = client.get("/stats")
    assert page.status_code == 200 and "noindex" in page.text


def test_a_frontier_pick_is_counted(client, monkeypatch):
    from app.engine import ENGINE
    from app.usage import USAGE, day_of
    from conftest import first

    chosen = first(sse(client.post("/api/run", json={"prompt": "a hard concurrency bug"})),
                   "decision")["selection"]["selected"]
    meta = dict(ENGINE.catalog_meta[chosen]); meta["frontier"] = True
    monkeypatch.setitem(ENGINE.catalog_meta, chosen, meta)
    before = USAGE.snapshot()["days"][day_of()]["frontierPicks"]
    sse(client.post("/api/run", json={"prompt": "a hard concurrency bug"}))
    assert USAGE.snapshot()["days"][day_of()]["frontierPicks"] == before + 1


def test_every_page_is_english_and_carries_the_install_line(client):
    import re

    german = re.compile(r"\b(und|nicht|oder|gemäß|Angaben|Haftung|Anschrift|Vertreten|Schreibe)\b")
    page = client.get("/impressum").text
    # The company's registered name is German and stays; nothing else may be.
    text = page.replace("Betriebs UG (haftungsbeschränkt)", "").replace(
        "Verwaltungs UG (haftungsbeschränkt)", "")
    assert not german.search(text), german.search(text)
    assert 'id="install-bar"' in page
    assert "curl -fsSL https://whichmodel.app.mintapis.com/install.sh | sh" in page
    examples = client.get("/api/meta").json()["examples"]
    assert not any(german.search(e["prompt"]) for e in examples)
