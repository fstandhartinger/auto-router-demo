"""robots.txt, sitemap.xml and JSON-LD for the demo site.

The page list comes from ``app.meta.PAGES``, so a page added there is in the
sitemap without a second edit. Nothing here states a number: the structured
data names the software, its licence and where the code is.
"""

from __future__ import annotations

import json

from . import meta as page_meta
from .settings import SETTINGS

#: Pages that are in the app but not worth a search result of their own.
#: "playground" is the home page under a second name and canonicalises to it.
UNLISTED = {"privacy", "impressum", "playground"}


def robots_txt() -> str:
    site = page_meta.SITE_URL
    return ("User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /stats\n\n"
            f"Sitemap: {site}/sitemap.xml\n")


def sitemap_xml() -> str:
    site = page_meta.SITE_URL
    urls = "".join(f"<url><loc>{site}/{path}</loc></url>" if path else f"<url><loc>{site}</loc></url>"
                   for path in page_meta.PAGES if path not in UNLISTED)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')


def json_ld(path: str) -> str:
    """SoftwareApplication on the home page; nothing elsewhere."""
    if path.strip("/") not in ("", "playground"):
        return ""
    page = page_meta.PAGES[""]
    data = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "auto-router",
        "alternateName": "auto-model-router",
        "url": page_meta.SITE_URL,
        "description": page.description,
        "applicationCategory": "DeveloperApplication",
        "operatingSystem": "macOS, Linux, Windows (WSL)",
        "license": "https://opensource.org/licenses/MIT",
        "isAccessibleForFree": True,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
        "sameAs": [SETTINGS.repo_url, SETTINGS.demo_repo_url],
        "author": {"@type": "Person", "name": "Florian Standhartinger"},
    }
    body = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">{body}</script>'
