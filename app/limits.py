"""Rate limits and the demo's money.

A public playground spends someone's money on every run, so the caps are
several and independent, and all of them are visible to the visitor:

* how many runs one address may start per hour and per day,
* how many runs one *network* may start per hour and per day, so that rotating
  through a /24 or a /64 is not a way around the address limit,
* how many runs the whole demo may start per day,
* how many dollars of metered provider spend the demo may use per day.

The money cap is different in kind from the others: when it is reached the
demo does not stop, it stops *paying*. Free routes keep answering and the
routing decision is still computed and shown, because that is the part of the
page that matters and it costs nothing.

Only counters are kept - a salted hash of the address, a number, a timestamp.
No prompt and no answer is stored, here or anywhere else in the process.
The salt lives in the state file, not in the repository, and is generated on
first start; it exists so a restart does not hand every visitor a fresh
allowance, and it is what keeps the stored hashes from being matchable against
a list of addresses later.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .settings import SETTINGS

log = logging.getLogger("demo.limits")

HOUR = 3600.0
DAY = 86400.0


def subnet_of(ip: str) -> str:
    """The block an address belongs to: /24 for IPv4, /64 for IPv6.

    Rotating addresses is cheap inside one of those and expensive across them,
    which is exactly the shape a per-visitor cap wants.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    prefix = 24 if addr.version == 4 else 64
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


@dataclass
class Verdict:
    allowed: bool
    reason: str = ""
    retry_after_s: int = 0


@dataclass
class Window:
    """Timestamps of the runs one key started, pruned to the last day."""

    hour: list[float] = field(default_factory=list)
    day: list[float] = field(default_factory=list)


@dataclass
class Limiter:
    per_ip_per_hour: int = SETTINGS.per_ip_per_hour
    per_ip_per_day: int = SETTINGS.per_ip_per_day
    per_subnet_per_hour: int = SETTINGS.per_subnet_per_hour
    per_subnet_per_day: int = SETTINGS.per_subnet_per_day
    global_per_day: int = SETTINGS.global_per_day
    daily_budget_usd: float = SETTINGS.daily_budget_usd
    state_path: str = ""
    _hits: dict[str, list[float]] = field(default_factory=dict)
    _day: float = 0.0
    _day_runs: int = 0
    _day_spend: float = 0.0
    _salt: bytes = b""
    _dirty: bool = False
    _saved_at: float = 0.0
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        if not self._salt:
            self._salt = os.urandom(16)
        if self.state_path:
            self._load()

    # -- identity ----------------------------------------------------------
    def client_key(self, ip: str) -> str:
        return "a:" + hashlib.sha256(self._salt + ip.encode()).hexdigest()[:16]

    def subnet_key(self, ip: str) -> str:
        return "n:" + hashlib.sha256(self._salt + subnet_of(ip).encode()).hexdigest()[:16]

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        path = Path(self.state_path)
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            self._save(force=True)
            return
        except Exception:  # noqa: BLE001 - a corrupt file must not stop the demo
            log.warning("limit state unreadable, starting from zero")
            return
        salt = raw.get("salt")
        if salt:
            try:
                self._salt = bytes.fromhex(salt)
            except ValueError:
                pass
        self._day = float(raw.get("day") or 0.0)
        self._day_runs = int(raw.get("dayRuns") or 0)
        self._day_spend = float(raw.get("daySpend") or 0.0)
        hits = raw.get("hits") or {}
        cutoff = time.time() - DAY
        self._hits = {k: [float(t) for t in v if float(t) > cutoff]
                      for k, v in hits.items() if isinstance(v, list)}
        self._hits = {k: v for k, v in self._hits.items() if v}

    def _save(self, force: bool = False) -> None:
        """Write the counters out. Called under the lock."""
        if not self.state_path:
            return
        now = time.time()
        if not force and now - self._saved_at < 5.0:
            self._dirty = True
            return
        payload = {"salt": self._salt.hex(), "day": self._day, "dayRuns": self._day_runs,
                   "daySpend": round(self._day_spend, 6), "hits": self._hits,
                   "savedAt": now}
        path = Path(self.state_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".limits-")
            with os.fdopen(fd, "w") as handle:
                json.dump(payload, handle)
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001 - a read-only volume must not stop the demo
            log.warning("could not persist limit state to %s", self.state_path)
            self.state_path = ""
            return
        self._saved_at = now
        self._dirty = False

    def flush(self) -> None:
        with self._lock:
            if self._dirty:
                self._save(force=True)

    # -- helpers -----------------------------------------------------------
    def _roll_day(self, now: float) -> None:
        day = now // DAY
        if day != self._day:
            self._day = day
            self._day_runs = 0
            self._day_spend = 0.0
            self._save(force=True)

    def _prune(self, now: float) -> None:
        cutoff = now - DAY
        for key in list(self._hits):
            kept = [t for t in self._hits[key] if t > cutoff]
            if kept:
                self._hits[key] = kept
            else:
                del self._hits[key]

    def _count(self, key: str, now: float, window: float) -> list[float]:
        cutoff = now - window
        return [t for t in self._hits.get(key, []) if t > cutoff]

    # -- api ---------------------------------------------------------------
    def check(self, key: str, subnet: str | None = None, now: float | None = None) -> Verdict:
        """May this visitor start a run right now?

        The money cap is deliberately not asked here: a spent budget pauses
        paid routes (see :meth:`may_spend`), it does not close the demo.
        """
        now = now or time.time()
        with self._lock:
            self._roll_day(now)
            self._prune(now)
            if self._day_runs >= self.global_per_day:
                return Verdict(False, "daily-cap", int(DAY - (now % DAY)))
            checks = [
                (key, HOUR, self.per_ip_per_hour, "per-ip"),
                (key, DAY, self.per_ip_per_day, "per-ip-day"),
            ]
            if subnet and subnet != key:
                checks += [
                    (subnet, HOUR, self.per_subnet_per_hour, "per-subnet"),
                    (subnet, DAY, self.per_subnet_per_day, "per-subnet-day"),
                ]
            for bucket, window, cap, reason in checks:
                hits = self._count(bucket, now, window)
                if len(hits) >= cap:
                    return Verdict(False, reason, int(max(1, hits[0] + window - now)))
            return Verdict(True)

    def record_run(self, key: str, subnet: str | None = None, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            self._roll_day(now)
            self._hits.setdefault(key, []).append(now)
            if subnet and subnet != key:
                self._hits.setdefault(subnet, []).append(now)
            self._day_runs += 1
            self._save()

    def record_spend(self, usd: float, now: float | None = None) -> None:
        if usd <= 0:
            return
        with self._lock:
            self._roll_day(now or time.time())
            self._day_spend += usd
            self._save(force=True)

    def budget_left(self, now: float | None = None) -> float:
        with self._lock:
            self._roll_day(now or time.time())
            return max(0.0, self.daily_budget_usd - self._day_spend)

    def paid_paused(self, now: float | None = None) -> bool:
        """Is the demo out of money for paid routes today?"""
        return self.budget_left(now) <= 0.0

    def may_spend(self, usd: float, now: float | None = None) -> bool:
        """Is there room for a call estimated at ``usd``?

        A free route always passes: that is what keeps the demo answering
        after the money is gone.
        """
        if usd <= 0:
            return True
        if usd > SETTINGS.max_call_usd:
            return False
        return self.budget_left(now) >= usd

    def snapshot(self, key: str | None = None, subnet: str | None = None,
                 now: float | None = None) -> dict:
        now = now or time.time()
        with self._lock:
            self._roll_day(now)
            self._prune(now)
            used_hour = len(self._count(key, now, HOUR)) if key else 0
            used_day = len(self._count(key, now, DAY)) if key else 0
            left = max(0.0, self.daily_budget_usd - self._day_spend)
            return {
                "perIpPerHour": self.per_ip_per_hour,
                "perIpPerDay": self.per_ip_per_day,
                "perIpUsed": used_hour,
                "perIpUsedToday": used_day,
                "perIpLeft": min(max(0, self.per_ip_per_hour - used_hour),
                                 max(0, self.per_ip_per_day - used_day)),
                "perSubnetPerHour": self.per_subnet_per_hour,
                "perSubnetPerDay": self.per_subnet_per_day,
                "runsToday": self._day_runs,
                "runsPerDay": self.global_per_day,
                "budgetUsd": round(self.daily_budget_usd, 2),
                "budgetLeftUsd": round(left, 4),
                "paidPaused": left <= 0.0,
                "maxCallUsd": SETTINGS.max_call_usd,
                "maxPromptChars": SETTINGS.max_prompt_chars,
                "maxOutputTokens": SETTINGS.max_output_tokens,
            }


LIMITER = Limiter(state_path=SETTINGS.state_file)


def client_key(ip: str) -> str:
    return LIMITER.client_key(ip)


def subnet_key(ip: str) -> str:
    return LIMITER.subnet_key(ip)
