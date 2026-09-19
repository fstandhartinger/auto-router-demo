"""How much the demo is used, counted without watching anyone.

The same approach as Benchmark Heaven's ``lib/visit-stats.mjs``, ported: the
server counts requests it serves anyway. Nothing is written to or read from the
visitor's device (no cookie, no storage, no script, no pixel), no identifier is
derived (no address, no hash, no fingerprint), and only daily totals are kept -
per page, per referring host, and per kind of event. Unique visitors are
therefore not measured; a "visit" is a page load that entered the site from
outside (no same-site referrer). Global Privacy Control and Do Not Track opt a
request out of the page counts. The user agent is read for the bot test only and
never stored.

What is counted, per UTC day:

* ``views`` / ``visits`` - page loads of the site's own pages by a browser,
* ``runs`` - playground runs, ``chats`` - turns on the multi-turn page,
* ``frontierPicks`` - turns where the router chose a frontier route,
* ``installs`` - downloads of ``install.sh`` (any client: that is its point),
* ``spendUsd`` - metered provider spend.

``boot`` changes on every process start. The counters live in a file inside
the container, so a redeploy starts them again from zero; the collector on the
host reads them per boot and adds the boots up, which is what makes the daily
history survive a redeploy.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger("demo.usage")

#: Days of history kept in the process. The host collector keeps the long tail.
RETENTION_DAYS = 400
#: No page or referrer with fewer loads than this is ever named in a report.
MIN_REPORT_COUNT = 3
#: Hard caps on distinct rows per day, so a scanner cannot grow the file.
MAX_PAGES_PER_DAY = 40
MAX_REFERRERS_PER_DAY = 200

OWN_HOSTS = {"whichmodel.app.mintapis.com", "localhost", "127.0.0.1"}

BOT_UA = re.compile(
    r"bot|crawl|spider|slurp|preview|fetch|scan|monitor|lighthouse|headless|phantom|playwright|"
    r"puppeteer|selenium|curl|wget|python|httpx|axios|node-fetch|undici|go-http|java/|okhttp|"
    r"libwww|facebookexternalhit|embedly|quora|whatsapp|telegram|discord|skype|vkshare|"
    r"w3c_validator|pingdom|uptime|gptbot|chatgpt|claude|anthropic|perplexity|bytespider|ccbot|"
    r"amazonbot|applebot|bingpreview", re.I)

EVENTS = ("runs", "chats", "frontierPicks", "installs")


def day_of(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts if ts is not None else time.time()))


def _host(value: str) -> str | None:
    try:
        host = urlsplit(value).hostname
    except ValueError:
        return None
    return host.lower().removeprefix("www.") if host else None


def classify_page_request(method: str, path: str, headers) -> dict | None:
    """Whether one request is a page view to count, and under which page.

    ``headers`` is anything with ``.get(name)``. Returns ``None`` for anything
    that is not a browser loading one of the site's pages.
    """
    if method != "GET":
        return None
    get = lambda name: headers.get(name) or ""  # noqa: E731
    if get("sec-gpc") == "1" or get("dnt") == "1":
        return None
    if re.search(r"prefetch|prerender", f"{get('sec-purpose')} {get('purpose')}", re.I):
        return None
    dest = get("sec-fetch-dest")
    if dest:
        if dest != "document":
            return None
    elif "text/html" not in get("accept"):
        return None
    ua = get("user-agent")
    if not ua or BOT_UA.search(ua):
        return None
    ref_host = _host(get("referer")) if get("referer") else None
    same_site = ref_host is not None and ref_host in OWN_HOSTS
    return {"path": "/" + path.strip("/"),
            "referrer": (ref_host or "")[:100] if not same_site else "",
            "visit": not same_site}


class Usage:
    """Daily totals in memory, written to a small JSON file now and then."""

    def __init__(self, state_path: str | None = None, flush_every_s: float = 60.0):
        self.state_path = Path(state_path) if state_path else None
        self.flush_every_s = flush_every_s
        self.boot = uuid.uuid4().hex[:12]
        self.started = time.time()
        self._lock = threading.Lock()
        self._days: dict[str, dict] = {}
        self._dirty = False
        self._saved_at = 0.0
        self._load()

    # -- recording ---------------------------------------------------------
    def _day(self, ts: float | None = None) -> dict:
        key = day_of(ts)
        row = self._days.get(key)
        if row is None:
            row = {"views": 0, "visits": 0, **{e: 0 for e in EVENTS}, "spendUsd": 0.0,
                   "pages": {}, "referrers": {}}
            self._days[key] = row
            self._prune()
        return row

    def page(self, hit: dict | None, ts: float | None = None) -> None:
        if not hit:
            return
        with self._lock:
            row = self._day(ts)
            row["views"] += 1
            if hit.get("visit"):
                row["visits"] += 1
            pages = row["pages"]
            if hit["path"] in pages or len(pages) < MAX_PAGES_PER_DAY:
                pages[hit["path"]] = pages.get(hit["path"], 0) + 1
            ref = hit.get("referrer") or ""
            refs = row["referrers"]
            if ref and (ref in refs or len(refs) < MAX_REFERRERS_PER_DAY):
                refs[ref] = refs.get(ref, 0) + 1
            self._dirty = True
        self._maybe_save()

    def event(self, name: str, n: int = 1, ts: float | None = None) -> None:
        if name not in EVENTS:
            raise ValueError(name)
        with self._lock:
            self._day(ts)[name] += n
            self._dirty = True
        self._maybe_save()

    def spend(self, usd: float, ts: float | None = None) -> None:
        if not usd:
            return
        with self._lock:
            row = self._day(ts)
            row["spendUsd"] = round(row["spendUsd"] + float(usd), 6)
            self._dirty = True
        self._maybe_save()

    # -- reading -----------------------------------------------------------
    def snapshot(self, days: int = 30) -> dict:
        """Raw per-day totals for the host collector: exact, per boot."""
        with self._lock:
            keys = sorted(self._days)[-max(1, days):]
            return {"boot": self.boot, "startedAt": round(self.started), "generatedAt": round(time.time()),
                    "days": {k: json.loads(json.dumps(self._days[k])) for k in keys}}

    def report(self, days: int = 30) -> dict:
        """What a person reads: totals, with small page and referrer counts folded away."""
        snap = self.snapshot(days)
        daily, pages, refs = [], {}, {}
        for day, row in sorted(snap["days"].items()):
            daily.append({"day": day, **{k: row[k] for k in ("views", "visits", *EVENTS, "spendUsd")}})
            for k, v in row["pages"].items():
                pages[k] = pages.get(k, 0) + v
            for k, v in row["referrers"].items():
                refs[k] = refs.get(k, 0) + v
        return {"boot": snap["boot"], "startedAt": snap["startedAt"], "daily": daily,
                "pages": _fold(pages), "referrers": _fold(refs)}

    # -- persistence -------------------------------------------------------
    def _prune(self) -> None:
        cutoff = day_of(time.time() - RETENTION_DAYS * 86400)
        for key in [k for k in self._days if k < cutoff]:
            del self._days[key]

    def _load(self) -> None:
        if not self.state_path or not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
            self._days = data.get("days") or {}
            # Counters that survived a crash-restart belong to the old boot as
            # far as the collector is concerned only if the boot id survives too.
            self.boot = data.get("boot") or self.boot
            self.started = data.get("startedAt") or self.started
        except (OSError, ValueError):
            log.warning("usage state unreadable; starting from zero")

    def _maybe_save(self) -> None:
        if time.time() - self._saved_at >= self.flush_every_s:
            self.flush()

    def flush(self) -> None:
        if not self.state_path:
            return
        with self._lock:
            if not self._dirty:
                return
            body = json.dumps({"boot": self.boot, "startedAt": self.started, "days": self._days})
            self._dirty = False
            self._saved_at = time.time()
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.state_path.parent), prefix=".usage-")
            with os.fdopen(fd, "w") as fh:
                fh.write(body)
            os.replace(tmp, self.state_path)
        except OSError:
            log.warning("could not write usage state")


def _fold(counts: dict[str, int]) -> list[dict]:
    rows = sorted(counts.items(), key=lambda kv: -kv[1])
    shown = [{"name": k, "count": v} for k, v in rows if v >= MIN_REPORT_COUNT][:25]
    rest = sum(v for k, v in rows) - sum(r["count"] for r in shown)
    if rest:
        shown.append({"name": "(other)", "count": rest})
    return shown


def _state_path() -> str:
    explicit = os.environ.get("DEMO_USAGE_FILE", "").strip()
    if explicit:
        return explicit
    limits = os.environ.get("DEMO_STATE_FILE", "/srv/state/limits.json")
    return str(Path(limits).with_name("usage.json"))


USAGE = Usage(_state_path())
