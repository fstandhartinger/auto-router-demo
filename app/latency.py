"""The other half of the objective: how long the visitor waits.

The published policy minimises expected dollars. On this playground almost every
candidate is free, so expected dollars is nearly flat for an easy question and
the tie falls to raw capability - which is how "What is the capital of
Australia?" reached a reasoning model and spent fourteen seconds thinking about
a one-line answer.

Two things live here:

* an expected time-to-answer per candidate, built from measured first-token
  latency and decode speed plus the reasoning tokens a model is expected to
  spend, and a policy that adds ``SECOND_USD * seconds`` to the expected cost;
* a reasoning plan per request, which is what actually removes most of the wait:
  an easy question is sent with thinking switched off where the provider
  supports it.

The weight matters more than the model. A second is priced at $0.002 (about
$7 an hour of someone's attention), which is enough to break a tie between
routes that are equally likely to be right and nowhere near enough to buy a
wrong answer: at high stakes the failure term of the published objective is
dollars, and no plausible amount of waiting outweighs it.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from auto_router.catalog import ModelInfo
from auto_router.policies import Context, Conversation, ExpectedCostPolicy, TurnRequest

from .settings import DATA

#: What one second of a visitor's waiting is worth, in dollars.
SECOND_USD = float(os.environ.get("DEMO_SECOND_USD", "") or 0.002)

#: Health file written by the host's free-worker prober every ~3 minutes.
HEALTH_FILE = Path(os.environ.get("DEMO_HEALTH_FILE", "") or "~/.llm-health.json").expanduser()
HEALTH_TTL_S = 30.0

#: A route whose prober says this or worse is not offered to the policy at all.
UNUSABLE_HEALTH = {"unhealthy", "locked"}
#: ... unless skipping it would leave nothing, in which case being slow beats
#: having no answer.
MIN_CANDIDATES = 1
#: How long a route sits out after it failed to answer, and the ceiling on that
#: when it keeps failing.
COOLDOWN_S = float(os.environ.get("DEMO_ROUTE_COOLDOWN_S", "") or 120.0)
MAX_COOLDOWN_S = 1800.0

#: Difficulty at or below which the demo asks for no thinking at all. Jev's
#: difficulty is already mapped onto 0..1 by the router's calibration.
NO_THINKING_MAX_DIFFICULTY = float(os.environ.get("DEMO_NO_THINK_MAX_DIFFICULTY", "") or 0.34)
#: ... and below which it asks for the provider's smallest thinking budget.
LOW_THINKING_MAX_DIFFICULTY = float(os.environ.get("DEMO_LOW_THINK_MAX_DIFFICULTY", "") or 0.67)
#: Hard ceiling on reasoning tokens in the demo, whatever the difficulty.
MAX_REASONING_TOKENS = int(os.environ.get("DEMO_MAX_REASONING_TOKENS", "") or 1024)

#: Fallbacks for a route we have never measured. Deliberately pessimistic: an
#: unmeasured route should not win a tie on an optimistic guess.
DEFAULT_TTFT_S = 3.0
DEFAULT_DECODE_TPS = 40.0
#: ... and what an unmeasured reasoning model is assumed to spend thinking, by
#: difficulty bucket. Near the low end of what the measured ones actually did.
DEFAULT_REASONING_TOKENS = {"easy": 100, "medium": 400, "hard": 700}


# ---------------------------------------------------------------------------
# measured speed: the file on disk, the host's prober, and this process
# ---------------------------------------------------------------------------
@dataclass
class Speed:
    """What one route does, in seconds and tokens per second."""

    ttft_s: float = DEFAULT_TTFT_S
    decode_tps: float = DEFAULT_DECODE_TPS
    #: Expected reasoning tokens when thinking is left on, by difficulty bucket.
    reasoning_tokens: dict[str, int] = field(default_factory=dict)
    source: str = "default"
    #: Extra queue wait the health prober is currently reporting.
    wait_s: float = 0.0
    health: str = "unknown"

    def seconds(self, output_tokens: int, reasoning_tokens: int) -> float:
        tps = self.decode_tps if self.decode_tps > 0 else DEFAULT_DECODE_TPS
        return self.wait_s + self.ttft_s + (output_tokens + reasoning_tokens) / tps


def _load_measured() -> dict:
    override = os.environ.get("DEMO_SPEED_FILE", "").strip()
    path = Path(override).expanduser() if override else DATA / "speed.measured.json"
    try:
        return json.loads(path.read_text()).get("routes") or {}
    except (OSError, ValueError, AttributeError):
        return {}


class SpeedBook:
    """Everything known about how fast each route answers.

    Three sources, most specific first: what this process has just seen, what
    the host's prober reported minutes ago, and what we measured once and
    committed. The first two are the reason a queue that fills up changes the
    decision instead of only the apology afterwards.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._measured = _load_measured()
        self._live: dict[str, tuple[float, float, int]] = {}
        #: model -> (consecutive failures, when, why)
        self._strikes: dict[str, tuple[int, float, str]] = {}
        self._health: dict = {}
        self._health_at = 0.0

    # -- host prober ------------------------------------------------------
    def _health_models(self) -> dict:
        now = time.time()
        if now - self._health_at < HEALTH_TTL_S:
            return self._health
        try:
            raw = json.loads(HEALTH_FILE.read_text())
        except (OSError, ValueError):
            raw = {}
        models = dict(raw.get("models") or {})
        # Union Alpha's routes are keyed separately and carry an explicit wait.
        for key, route in ((raw.get("union_alpha") or {}).get("routes") or {}).items():
            models[key] = route
        with self._lock:
            self._health = models
            self._health_at = now
        return models

    # -- this process -----------------------------------------------------
    def penalise(self, model: str, reason: str) -> None:
        """A route that just failed to answer is sat out for a while.

        The host's prober is not reachable from inside the deployed container,
        so this is the health signal that actually works in production: a route
        that missed its deadline or errored is skipped until the cooldown
        passes, instead of being offered to the next visitor unchanged.
        """
        with self._lock:
            strikes, _, _ = self._strikes.get(model, (0, 0.0, ""))
            self._strikes[model] = (strikes + 1, time.time(), reason)

    def cooling_off(self, model: str) -> bool:
        with self._lock:
            entry = self._strikes.get(model)
        if not entry:
            return False
        strikes, at, _ = entry
        # Doubling, so one bad minute does not retire a route for the day.
        return time.time() - at < min(COOLDOWN_S * strikes, MAX_COOLDOWN_S)

    def forgive(self, model: str) -> None:
        """A route that answered is healthy again, whatever it did before."""
        with self._lock:
            self._strikes.pop(model, None)

    def observe(self, model: str, ttft_s: float, decode_tps: float, tokens: int) -> None:
        """Fold one real answer into the running estimate for its route."""
        if tokens < 20 or not math.isfinite(ttft_s) or ttft_s <= 0:
            return
        with self._lock:
            prev = self._live.get(model)
            if prev is None:
                self._live[model] = (ttft_s, decode_tps, 1)
                return
            p_ttft, p_tps, n = prev
            # A plain running mean, capped so one stale sample cannot pin the
            # estimate: recent behaviour is what the next visitor will get.
            n = min(n + 1, 20)
            self._live[model] = (p_ttft + (ttft_s - p_ttft) / n,
                                 p_tps + (decode_tps - p_tps) / n, n)

    # -- lookup -----------------------------------------------------------
    def speed(self, model_name: str, health_key: str | None = None) -> Speed:
        entry = dict(self._measured.get(model_name) or {})
        speed = Speed(
            ttft_s=float(entry.get("ttft_s") or DEFAULT_TTFT_S),
            decode_tps=float(entry.get("decode_tps") or DEFAULT_DECODE_TPS),
            reasoning_tokens=dict(entry.get("reasoning_tokens") or {}),
            source="measured" if entry else "default",
        )
        health = self._health_models().get(health_key or model_name) or {}
        if health:
            speed.health = str(health.get("health") or "unknown")
            probe = health.get("median_probe_ttft_s")
            if probe:
                speed.ttft_s = float(probe)
                speed.source = "health"
            wait = health.get("current_wait_s")
            if wait:
                speed.wait_s = float(wait)
            # A busy pool answers later than an idle one; the prober measures
            # utilisation, so say so instead of pretending the queue is empty.
            util = health.get("utilization_5m")
            if util and float(util) > 0.75:
                speed.wait_s += 5.0 * (float(util) - 0.75) / 0.25
        with self._lock:
            live = self._live.get(model_name)
        if live:
            speed.ttft_s, speed.decode_tps, _ = live[0], live[1], live[2]
            speed.source = "live"
        return speed

    def snapshot(self) -> dict:
        with self._lock:
            live = {name: {"ttftS": round(t, 2), "decodeTps": round(d, 1), "samples": n}
                    for name, (t, d, n) in self._live.items()}
            for name, (strikes, _at, reason) in self._strikes.items():
                live.setdefault(name, {}).update(failures=strikes, lastFailure=reason)
        return live


BOOK = SpeedBook()


# ---------------------------------------------------------------------------
# reasoning: how much thinking this request is worth
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReasoningPlan:
    """What to ask the provider for, and what that should cost in tokens."""

    level: str                 # "off" | "low" | "full"
    budget_tokens: int
    request_extra: dict
    note: str


def plan_reasoning(model_name: str, difficulty: float, meta: dict) -> ReasoningPlan:
    """Decide how hard a route should think about this request.

    A model that does not reason gets an empty plan whatever the difficulty, so
    the page never claims to have switched off something that was never on.
    """
    speed = (meta or {}).get("speed") or {}
    dialect = speed.get("thinking_dialect")
    if not speed.get("reasoning"):
        return ReasoningPlan("none", 0, {}, "")
    if not dialect:
        # It reasons and we have no tested way to ask it not to. Saying "none"
        # here would quietly price it as if it never thought, which is how a
        # slow route wins a race it would lose.
        return ReasoningPlan("full", 0, {}, "")
    if difficulty <= NO_THINKING_MAX_DIFFICULTY:
        off = _dialect_extra(dialect, "off")
        if off is not None:
            return ReasoningPlan("off", 0, off,
                                 "easy request: answered without a thinking pass")
    budget = MAX_REASONING_TOKENS if difficulty > LOW_THINKING_MAX_DIFFICULTY \
        else MAX_REASONING_TOKENS // 4
    low = _dialect_extra(dialect, "low", budget)
    if low is None:
        return ReasoningPlan("full", budget, {}, "")
    return ReasoningPlan("low" if difficulty <= LOW_THINKING_MAX_DIFFICULTY else "full",
                         budget, low,
                         f"thinking capped at {budget} tokens for this demo")


#: Only dialects that were actually measured against the live endpoint on
#: 18 Sep 2026 are listed, because every family spells this differently and
#: most ignore an unknown key in silence. Sending an unproven one is worse than
#: sending none: it reads as if thinking had been switched off when it has not.
#: Two families were measured, one per endpoint kind the demo talks to.
DIALECTS = {
    # Templated open-weight routes: the OpenAI-style ``reasoning`` block is
    # accepted and ignored; only this one takes effect. Measured on two routes,
    # reasoning tokens to zero, 3.1s -> 1.5s and 2.4s -> 1.7s.
    "chat_template_kwargs": {"off": {"chat_template_kwargs": {"enable_thinking": False}}},
    # Hosted routes behind an OpenAI-compatible gateway: ``effort`` is honoured,
    # measured 10.2s -> 1.2s with no reasoning at all. ``enabled: false`` and
    # ``max_tokens: 0`` are refused there with "Reasoning is mandatory for this
    # endpoint", so this family only ever gets an effort string.
    "reasoning_effort": {"off": {"reasoning": {"effort": "low"}},
                         "low": {"reasoning": {"effort": "low"}}},
}


def _dialect_extra(dialect: str, level: str, budget: int = 0) -> dict | None:
    """The request body this provider family was measured to honour."""
    return (DIALECTS.get(dialect) or {}).get(level)


# ---------------------------------------------------------------------------
# the objective
# ---------------------------------------------------------------------------
def expected_seconds(model: ModelInfo, req: TurnRequest, meta: dict,
                     plan: ReasoningPlan | None = None, output_tokens: int | None = None) -> float:
    """How long this route is expected to take to finish this turn.

    ``output_tokens`` overrides the request's estimate. The policy leaves it
    alone, so its time term and its money term are priced on the same turn. The
    page passes the demo's own output cap, because that is the number the
    visitor is actually waiting for - showing the policy's 4000-token estimate
    next to an answer that arrives in two seconds would just be untrue.
    """
    speed = BOOK.speed(model.name, ((meta or {}).get("speed") or {}).get("health_key"))
    plan = plan or plan_reasoning(model.name, req.difficulty, meta)
    bucket = "easy" if req.difficulty < 0.34 else "medium" if req.difficulty < 0.67 else "hard"
    if plan.level in ("off", "none"):
        thinking = 0
    elif plan.budget_tokens:
        thinking = plan.budget_tokens
    else:
        # Left to think as much as it likes: what it was measured to spend, or
        # the pessimistic default if this route was never measured.
        thinking = int(speed.reasoning_tokens.get(bucket)
                       or DEFAULT_REASONING_TOKENS.get(bucket, 0))
    out = req.output_tokens if output_tokens is None else min(req.output_tokens, output_tokens)
    seconds = speed.seconds(out, thinking)
    return seconds * max(1, req.steps)


def route_is_skippable(model_name: str, meta: dict) -> bool:
    """A route that should not be offered for the next decision.

    Either the host's prober calls it unhealthy - which only the local runs can
    see, since the deployed container cannot read that file - or this process
    watched it fail to answer and it is still cooling off.
    """
    key = ((meta or {}).get("speed") or {}).get("health_key") or model_name
    if BOOK.cooling_off(model_name):
        return True
    return BOOK.speed(model_name, key).health in UNUSABLE_HEALTH


class LatencyAwarePolicy(ExpectedCostPolicy):
    """The published expected-cost policy, plus the visitor's time.

    Only :meth:`value` changes: the score a candidate is ranked by becomes
    ``expected dollars + SECOND_USD * expected seconds``. Everything else -
    the failure term, the retry model, the cache horizon, the switch margin -
    is the published policy's, unmodified.
    """

    name = "F_expected_latency"

    def __init__(self, *args, meta: dict | None = None, second_usd: float = SECOND_USD, **kwargs):
        super().__init__(*args, **kwargs)
        self.meta = meta if meta is not None else {}
        self.second_usd = second_usd

    def seconds_for(self, model: ModelInfo, req: TurnRequest) -> float:
        return expected_seconds(model, req, self.meta.get(model.name, {}))

    def value(self, m: ModelInfo, conv: Conversation, req: TurnRequest, ctx: Context, d: float,
              pool: list[ModelInfo]) -> tuple[float, float]:
        money, p = super().value(m, conv, req, ctx, d, pool)
        if not math.isfinite(money):
            return money, p
        return money + self.second_usd * self.seconds_for(m, req), p
