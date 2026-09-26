"""Link previews: what a crawler gets, without running any JavaScript.

Every assertion here is about the *first response's bytes*, because that is all
Twitterbot, facebookexternalhit, Slackbot, WhatsApp, TelegramBot, LinkedInBot
and Discordbot ever read. A tag that only appears after the app boots is, to
them, a tag that does not exist.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import meta
from app.main import app

BOTS = {
    "twitter": "Twitterbot/1.0",
    "facebook": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "whatsapp": "WhatsApp/2.23.20.0",
    "slack": "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "telegram": "TelegramBot (like TwitterBot)",
    "linkedin": "LinkedInBot/1.0 (compatible; Mozilla/5.0; Jakarta Commons-HttpClient/3.1)",
    "discord": "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
}

REQUIRED = ("og:title", "og:description", "og:url", "og:type", "og:image",
            "og:image:width", "og:image:height", "twitter:card", "twitter:image")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.parametrize("agent", sorted(BOTS), ids=sorted(BOTS))
def test_each_crawler_gets_every_tag_it_needs(client, agent):
    html = client.get("/", headers={"User-Agent": BOTS[agent]}).text
    for tag in REQUIRED:
        assert f'"{tag}"' in html, f"{agent} would not see {tag}"
    assert 'content="summary_large_image"' in html


def test_a_subpage_previews_as_itself_not_as_the_playground(client):
    how = client.get("/how").text
    home = client.get("/").text
    assert "<title>How it works" in how
    assert "<title>Auto-router: an open-source LLM router" in home
    assert 'href="https://whichmodel.app.mintapis.com/how"' in how
    assert 'content="https://whichmodel.app.mintapis.com/how"' in how


def test_the_image_url_is_absolute():
    """A relative og:image is dropped by most crawlers, silently."""
    assert 'content="https://whichmodel.app.mintapis.com/assets/og-card.png"' in meta.head_for("")


def test_the_image_exists_is_a_png_and_is_small_enough(client):
    response = client.get("/assets/og-card.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(response.content) < 300_000, "crawlers drop heavy preview images"


def test_the_icons_are_served(client):
    for path, prefix in (("/assets/favicon.ico", b"\x00\x00\x01\x00"),
                         ("/assets/apple-touch-icon.png", b"\x89PNG"),
                         ("/assets/mark.svg", b"<svg")):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.content.startswith(prefix), path


def test_an_unknown_path_still_previews_as_the_site(client):
    html = client.get("/does-not-exist").text
    assert "<title>Auto-router: an open-source LLM router" in html
    assert 'content="https://whichmodel.app.mintapis.com"' in html


def test_the_template_keeps_its_marker():
    """The substitution is silent when the marker is gone; this test is not."""
    template = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text()
    assert meta.MARKER in template
    assert "<title>" not in template, "the title belongs to meta.py, one per page"
