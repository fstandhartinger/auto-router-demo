"""Rate limits and the demo's money.

A public playground spends someone's money on every run, so three separate
things are capped and all three are visible to the visitor:

* how many runs one IP address may start per hour,
* how many runs the whole demo may start per day,
* how many dollars of metered provider spend the demo may use per day.

Only counters are kept - a truncated hash of the address, a number, a timestamp.
No prompt and no answer is stored, here or anywhere else in the process.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field

from .settings import SETTINGS

HOUR = 3600.0
DAY = 86400.0

#: Rotates on restart, so the stored hashes cannot be matched against an
#: address list later.
_SALT = os.urandom(16)


def client_key(ip: str) -> str:
    return hashlib.sha256(_SALT + ip.encode()).hexdigest()[:16]


@dataclass
class Verdict:
    allowed: bool
    reason: str = ""
    retry_after_s: int = 0


@dataclass
class Limiter:
    per_ip_per_hour: int = SETTINGS.per_ip_per_hour
    global_per_day: int = SETTINGS.global_per_day
    daily_budget_usd: float = SETTINGS.daily_budget_usd
    _hits: dict[str, list[float]] = field(default_factory=dict)
    _day: float = 0.0
    _day_runs: int = 0
    _day_spend: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- helpers -----------------------------------------------------------
    def _roll_day(self, now: float) -> None:
        day = now // DAY
        if day != self._day:
            self._day = day
            self._day_runs = 0
            self._day_spend = 0.0

    def _prune(self, now: float) -> None:
        cutoff = now - HOUR
        for key in list(self._hits):
            kept = [t for t in self._hits[key] if t > cutoff]
            if kept:
                self._hits[key] = kept
            else:
                del self._hits[key]

    # -- api ---------------------------------------------------------------
    def check(self, key: str, now: float | None = None) -> Verdict:
        """May this visitor start a run right now?"""
        now = now or time.time()
        with self._lock:
            self._roll_day(now)
            self._prune(now)
            if self._day_runs >= self.global_per_day:
                return Verdict(False, "daily-cap", int(DAY - (now % DAY)))
            if self._day_spend >= self.daily_budget_usd:
                return Verdict(False, "budget", int(DAY - (now % DAY)))
            hits = self._hits.get(key, [])
            if len(hits) >= self.per_ip_per_hour:
                return Verdict(False, "per-ip", int(max(1, hits[0] + HOUR - now)))
            return Verdict(True)

    def record_run(self, key: str, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            self._roll_day(now)
            self._hits.setdefault(key, []).append(now)
            self._day_runs += 1

    def record_spend(self, usd: float, now: float | None = None) -> None:
        if usd <= 0:
            return
        with self._lock:
            self._roll_day(now or time.time())
            self._day_spend += usd

    def budget_left(self, now: float | None = None) -> float:
        with self._lock:
            self._roll_day(now or time.time())
            return max(0.0, self.daily_budget_usd - self._day_spend)

    def may_spend(self, usd: float, now: float | None = None) -> bool:
        """Is there room for a call estimated at ``usd``?"""
        if usd <= 0:
            return True
        if usd > SETTINGS.max_call_usd:
            return False
        return self.budget_left(now) >= usd

    def snapshot(self, key: str | None = None, now: float | None = None) -> dict:
        now = now or time.time()
        with self._lock:
            self._roll_day(now)
            self._prune(now)
            used = len(self._hits.get(key, [])) if key else 0
            return {
                "perIpPerHour": self.per_ip_per_hour,
                "perIpUsed": used,
                "perIpLeft": max(0, self.per_ip_per_hour - used),
                "runsToday": self._day_runs,
                "runsPerDay": self.global_per_day,
                "budgetUsd": round(self.daily_budget_usd, 2),
                "budgetLeftUsd": round(max(0.0, self.daily_budget_usd - self._day_spend), 4),
                "maxCallUsd": SETTINGS.max_call_usd,
                "maxPromptChars": SETTINGS.max_prompt_chars,
                "maxOutputTokens": SETTINGS.max_output_tokens,
            }


LIMITER = Limiter()
