"""Render the link-preview image and the icons, deterministically.

A shared link is often the only part of this site a person ever sees, so the
card is built from the same palette, type scale and hairlines as the page - by
rendering real HTML in the same browser engine, not by drawing a picture that
will drift from the page the first time the page changes.

    python scripts/preview/build.py          # writes static/assets/og-card.png,
                                             # apple-touch-icon.png, favicon.ico

Everything is local: system fonts, no network, no webfonts. The output is
checked in, because a crawler fetches it in the second after someone pastes the
link and nothing may be generated on demand at that moment.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CARD = Path(__file__).resolve().parent / "card.html"
ASSETS = ROOT / "static" / "assets"

#: Open Graph's documented size. Twitter, Slack, WhatsApp, LinkedIn, Discord and
#: Telegram all render this ratio; anything much larger than a few hundred
#: kilobytes gets dropped by at least one of them.
WIDTH, HEIGHT = 1200, 630
MAX_BYTES = 300_000


async def render() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT},
                                      device_scale_factor=1)
        await page.goto(CARD.as_uri())
        await page.screenshot(path=str(ASSETS / "og-card.png"))
        await browser.close()


def icons() -> None:
    """A 180x180 touch icon and a multi-size .ico, both from the brand mark."""
    mark = ASSETS / "mark.svg"
    subprocess.run(["convert", "-background", "none", "-density", "600",
                    str(mark), "-resize", "180x180", str(ASSETS / "apple-touch-icon.png")],
                   check=True)
    subprocess.run(["convert", "-background", "none", "-density", "600", str(mark),
                    "-define", "icon:auto-resize=16,32,48", str(ASSETS / "favicon.ico")],
                   check=True)


def main() -> None:
    asyncio.run(render())
    icons()
    card = ASSETS / "og-card.png"
    size = card.stat().st_size
    if size > MAX_BYTES:
        # Crawlers silently skip an image that is too heavy, and a silent skip
        # is indistinguishable from a broken tag - so fail loudly here instead.
        subprocess.run(["convert", str(card), "-strip", "-colors", "255", str(card)], check=True)
        size = card.stat().st_size
    print(f"og-card.png {size // 1024} kB, apple-touch-icon.png, favicon.ico")
    if size > MAX_BYTES:
        sys.exit(f"og-card.png is {size} bytes, over the {MAX_BYTES} budget")


if __name__ == "__main__":
    main()
