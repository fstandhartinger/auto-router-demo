"""Per-page head metadata, rendered into the HTML before it is sent.

The site is a single-page app: every route is served from the same
``index.html`` and the view is built in the browser. A crawler does not run
that JavaScript. Twitterbot, facebookexternalhit, Slackbot, WhatsApp,
TelegramBot, LinkedInBot and Discordbot all read the bytes of the first
response and stop, so a shared link to ``/how`` would otherwise preview as the
playground - or, with no image tag at all, as a bare grey box.

So the head is assembled here, on the server, per path, and substituted into
the template once at startup. It costs one string replace per request against a
template that is already in memory, and it keeps the first HTML response the
same size it was.

The image is a checked-in PNG (``scripts/preview/build.py``), never generated on
demand: a crawler fetches it in the second after someone pastes the link.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass

#: Absolute origin for canonical and Open Graph URLs. Crawlers reject relative
#: image URLs, so this has to be configurable per deployment rather than
#: guessed from the request - a proxy's Host header is not something to trust
#: with what the whole internet then caches.
SITE_URL = os.environ.get("SITE_URL", "https://whichmodel.app.mintapis.com").rstrip("/")

#: The account a preview card is attributed to, or "" for no attribution tag.
TWITTER_SITE = os.environ.get("TWITTER_SITE", "@airesearch12")

OG_IMAGE = "/assets/og-card.png"
OG_IMAGE_ALT = ("The auto-router playground: candidate models priced for one request, the "
                "chosen route highlighted, and a chip reading Checked by Jev.")


@dataclass(frozen=True)
class PageMeta:
    title: str
    description: str
    #: Path as the crawler will see it, without a trailing slash.
    path: str = ""


#: One entry per routable path. The descriptions are what a person sees in a
#: chat client before they decide whether to open the link, so each says what
#: *that* page does rather than repeating the site's tagline.
PAGES: dict[str, PageMeta] = {
    "": PageMeta(
        "Auto-router playground — watch a router pick the model",
        "Type a prompt. Jev classifies it in about 0.6 s, every candidate model is priced for "
        "that exact request from Benchmark Heaven data, the expected-cost rule picks one, and "
        "the answer streams back — with a Jev check on the cheap answers.",
    ),
    "cache": PageMeta(
        "Why switching models costs money — auto-router playground",
        "A cached prefix is read at about a tenth of the input price; the same prefix on another "
        "model is written from scratch. See what that does to a conversation's bill.",
    ),
    "results": PageMeta(
        "Results: 78 graded tasks, a week of replayed traffic — auto-router",
        "What the router was measured on, what it got right, and what the numbers do not show. "
        "Including the answer judge: what it catches, what it costs, what it escalates.",
    ),
    "how": PageMeta(
        "How it works: classify, decide, answer, check — auto-router",
        "Three steps and a verdict. Jev classifies the request, the expected-cost rule prices "
        "every route including its cache state, the chosen model answers, and a cheap answer is "
        "checked before you get it.",
    ),
    "run": PageMeta(
        "Run the router on your own machine — auto-router",
        "Run it locally in front of Claude Code, Codex, opencode or Cursor. Use requests included "
        "with your plans when they fit, and cheaper models on your own keys for easy turns.",
    ),
    "privacy": PageMeta(
        "Privacy — auto-router playground",
        "What this demo stores, what it does not, and who sees a prompt.",
    ),
    "impressum": PageMeta(
        "Legal notice — auto-router playground",
        "Provider identification under German law.",
    ),
}


def _tag(name: str, value: str, *, attr: str = "property") -> str:
    return f'<meta {attr}="{name}" content="{html.escape(value, quote=True)}">'


def head_for(path: str) -> str:
    """The full per-page head block: title, description, Open Graph, Twitter, icons."""
    page = PAGES.get(path.strip("/"), PAGES[""])
    url = f"{SITE_URL}/{path.strip('/')}".rstrip("/")
    image = f"{SITE_URL}{OG_IMAGE}"
    lines = [
        f"<title>{html.escape(page.title)}</title>",
        _tag("description", page.description, attr="name"),
        f'<link rel="canonical" href="{url}">',
        _tag("og:type", "website"),
        _tag("og:site_name", "auto-router playground"),
        _tag("og:title", page.title),
        _tag("og:description", page.description),
        _tag("og:url", url),
        _tag("og:image", image),
        _tag("og:image:width", str(1200)),
        _tag("og:image:height", str(630)),
        _tag("og:image:type", "image/png"),
        _tag("og:image:alt", OG_IMAGE_ALT),
        _tag("twitter:card", "summary_large_image", attr="name"),
        _tag("twitter:title", page.title, attr="name"),
        _tag("twitter:description", page.description, attr="name"),
        _tag("twitter:image", image, attr="name"),
        _tag("twitter:image:alt", OG_IMAGE_ALT, attr="name"),
        '<link rel="icon" href="/assets/favicon.ico" sizes="32x32">',
        '<link rel="icon" href="/assets/mark.svg" type="image/svg+xml">',
        '<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">',
    ]
    if TWITTER_SITE:
        lines.append(_tag("twitter:site", TWITTER_SITE, attr="name"))
    return "\n".join(lines)


#: Where ``head_for`` output goes in the template.
MARKER = "<!--page-meta-->"


def render(template: str, path: str) -> str:
    """``index.html`` with this path's head block in place of the marker."""
    return template.replace(MARKER, head_for(path))
