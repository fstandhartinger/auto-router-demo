"""Search-engine files and status codes: nothing a crawler reads is a copy of the home page."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app import meta
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_robots_is_text_and_points_at_the_sitemap(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert f"Sitemap: {meta.SITE_URL}/sitemap.xml" in r.text
    assert "Disallow: /api/" in r.text


def test_sitemap_lists_only_real_pages(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    locs = re.findall(r"<loc>([^<]+)</loc>", r.text)
    assert meta.SITE_URL in locs and f"{meta.SITE_URL}/run" in locs
    assert f"{meta.SITE_URL}/impressum" not in locs
    for loc in locs:
        path = loc[len(meta.SITE_URL):] or "/"
        assert client.get(path).status_code == 200, path


def test_an_unknown_path_is_a_real_404_with_noindex(client):
    r = client.get("/does-not-exist")
    assert r.status_code == 404
    assert '<meta name="robots" content="noindex">' in r.text
    assert "application/ld+json" not in r.text


def test_home_has_valid_software_application_json_ld(client):
    html = client.get("/").text
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert len(blocks) == 1
    data = json.loads(blocks[0])
    assert data["@type"] == "SoftwareApplication"
    assert data["license"].endswith("MIT") and data["offers"]["price"] == "0"
    assert 'name="robots"' not in html
    # Sub-pages carry no home-page structured data.
    assert "application/ld+json" not in client.get("/how").text
