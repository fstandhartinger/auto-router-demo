import httpx


def test_shell_loads_pinned_tracker_and_footer_counter(client):
    page = client.get("/").text
    assert '<script defer src="/analytics.js"></script>' in page
    assert 'id="visitor-count"' in page
    assert "d3f0e8b5-20e0-4649-8bad-2ba3c26eabe6" in client.get("/analytics.js").text
    assert "https://bh-analytics.app.mintapis.com/script.js" in client.get("/analytics.js").text


def test_analytics_proxy_is_server_side_and_returns_only_cached_aggregate(client, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "UMAMI_API_KEY", "server-test-key")
    monkeypatch.setattr(main, "_UMAMI_VISITS_CACHE", {"visits": None, "expires_at": 0, "retry_after": 0})
    calls = []

    async def fake_get(url, *, params, headers, timeout):
        calls.append((url, params, headers, timeout))
        return httpx.Response(200, json={"visitors": 421}, request=httpx.Request("GET", url))

    monkeypatch.setattr(main.app.state.client, "get", fake_get)
    first = client.get("/api/analytics/visits")
    second = client.get("/api/analytics/visits")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"visits": 421}
    assert "server-test-key" not in first.text
    assert len(calls) == 1
    assert calls[0][0] == f"{main.UMAMI_API_URL}/api/websites/{main.UMAMI_WEBSITE_ID}/stats"
    assert calls[0][2] == {"Authorization": "Bearer server-test-key"}
    assert calls[0][3] == 3.0


def test_analytics_proxy_fails_closed_without_key(client, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "UMAMI_API_KEY", "")
    response = client.get("/api/analytics/visits")
    assert response.status_code == 503
    assert response.json() == {"detail": "analytics unavailable"}


def test_tracker_drops_non_pageview_payloads_and_unapproved_url_data():
    from pathlib import Path

    script = Path("static/analytics.js").read_text()
    assert "navigator.globalPrivacyControl !== true" in script
    assert "navigator.doNotTrack" in script
    assert "hasOwnProperty.call(payload, 'name')" in script
    assert "hasOwnProperty.call(payload, 'data')" in script
    assert "url: page.pathname" in script
    assert "referrer: ''" in script
    assert "page.search" not in script
