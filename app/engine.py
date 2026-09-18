"""The demo's wiring around the published router.

The catalog, the cost model, the success model, the expected-cost policy and
the decision record all come from the ``auto_router`` package pinned in the
Dockerfile; this module builds the configuration, decides which routes exist
right now, and turns one decision into the JSON the page draws.

One thing about the routing logic does live in this app, and it is worth being
explicit about: the policy the router is given is the published one plus the
visitor's time (``app/latency.py``). The money half is untouched.
"""

from __future__ import annotations

import copy
import json
import math
import os
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from auto_router import jev
from auto_router.bench import BenchmarkClient
from auto_router.cache_index import estimate_tokens
from auto_router.catalog import Catalog, ModelInfo
from auto_router.config import Provider, RouterConfig, build_model
from auto_router.economics import SuccessModel, turn_cost
from auto_router.policies import Context, Conversation, TurnRequest, turn_call_cost
from auto_router.router import Router

from . import providers
from . import latency
from .bonsai import MODEL_NAME as BONSAI_MODEL
from .settings import DATA, SETTINGS

#: Jev reports difficulty on a compressed scale; this maps it back. Measured on
#: the 78-task set (EXPERIMENTS.md section 4).
JEV_CALIBRATION = [0.27, 0.51]

#: Which answers get a second opinion, and against what. These are the
#: published router's own calibrated numbers, written out here so the page can
#: show a visitor exactly what it is applying rather than making them read a
#: library default. ``max_capability`` is what keeps a frontier route out: Jev
#: is not stronger than one, and grading one would produce false alarms.
VERIFY_POLICY = {
    "enabled": True,
    "max_price_per_mtok": 1.0,
    "max_capability": 72.0,
    "skip_categories": ["long_context"],
}


def _load(name: str):
    """Read one data file, honouring an override so tests can run hermetically."""
    override = os.environ.get(f"DEMO_{name.split('.')[0].upper()}_FILE", "").strip()
    path = Path(override).expanduser() if override else DATA / name
    return json.loads(path.read_text())


def _success_model() -> SuccessModel:
    measured = {(m, c, b): p for m, c, b, p in _load("success.measured.json")}
    return SuccessModel(measured=measured)


def _provider_objects() -> dict[str, Provider]:
    out: dict[str, Provider] = {}
    for name, p in ((SETTINGS.routes or {}).get("providers") or {}).items():
        out[name] = Provider(name=name, base_url=str(p.get("base_url", "")).rstrip("/"),
                             api_key_env=p.get("api_key_env"), cache=p.get("cache", "generic"),
                             extra_headers=p.get("extra_headers") or {})
    return out


@dataclass
class Decision:
    """One routing decision plus everything the page draws around it."""

    model: ModelInfo
    request: TurnRequest
    classification: jev.Classification | None
    explanation: object
    context: Context
    conversation_id: str
    classification_ms: float
    now: float
    reason: str
    #: The uncalibrated char/4 estimate, kept so the estimator can learn from
    #: the provider's real token count once the answer comes back.
    raw_tokens: int = 0
    #: How hard the chosen route is asked to think about this request.
    reasoning: object = None


class Engine:
    """Builds the catalog once, then answers routing questions from it."""

    def __init__(self, settings=SETTINGS):
        self.settings = settings
        self.built_at = 0.0
        self.bench_errors: list[str] = []
        self._lock = threading.Lock()
        self.catalog_meta: dict[str, dict] = {}
        self.baseline: str | None = None
        self.router: Router | None = None
        self.config: RouterConfig | None = None

    # -- construction ------------------------------------------------------
    def build(self) -> None:
        """Assemble the catalog from the benchmark API plus our measurements."""
        entries = _load("catalog.json")["models"]
        route_entries = (self.settings.routes or {}).get("routes") or {}
        provider_objs = _provider_objects()
        bench = BenchmarkClient(offline=os.environ.get("AUTO_ROUTER_BENCH_OFFLINE") == "1")
        models: list[ModelInfo] = []
        meta: dict[str, dict] = {}
        raw_entries: list[dict] = []
        for entry in entries:
            demo = dict(entry.pop("demo", {}) or {})
            route = route_entries.get(entry["name"]) or {}
            config_entry = {k: v for k, v in entry.items() if v is not None}
            if route.get("provider"):
                config_entry["provider"] = route["provider"]
            try:
                model = build_model(config_entry, provider_objs, bench)
            except ValueError:
                # No prices: the benchmark API had no usable offer for this
                # model and the config sets none. Skip it rather than guess.
                continue
            models.append(model)
            raw_entries.append(config_entry)
            demo["executable"] = bool(providers.route_for(model.name))
            meta[model.name] = demo
            if demo.get("baseline"):
                self.baseline = model.name
        self.bench_errors = list(bench.errors)
        with self._lock:
            self.catalog_meta = meta
            self.config = RouterConfig(
                providers=provider_objs, catalog=Catalog(models), subscriptions={},
                policy={"name": "F_expected", "jev_difficulty_calibration": JEV_CALIBRATION,
                        "verify": VERIFY_POLICY},
                raw={"models": raw_entries})
            # The published expected-cost policy, plus the visitor's time. The
            # money half is untouched; see app/latency.py for what is added and
            # what a second is priced at.
            self.router = Router(self.config, policy=latency.LatencyAwarePolicy(meta=meta),
                                 success=_success_model(), classifier=None,
                                 quota_reader=lambda: {})
            self.built_at = time.time()

    def ready(self) -> bool:
        return self.router is not None and len(self.config.catalog.all()) > 0

    @property
    def verify_policy(self):
        """Which answers are checked. The router's own object, not a copy.

        Read through the router rather than rebuilt here so the page, the cost
        model and the runtime gate can never drift apart: the same object
        decides what is checked and prices the check into every candidate.
        """
        from auto_router.verify import VerifyPolicy
        return self.router.verify if self.router is not None else VerifyPolicy()

    # -- catalog views -----------------------------------------------------
    def context(self, *, exclude: set[str] | None = None) -> Context:
        ctx = self.router.context()
        if exclude:
            ctx = Context(Catalog([m for m in ctx.catalog.all() if m.name not in exclude]),
                          ctx.success, ctx.quota, ctx.subscription_reference)
        return ctx

    def excluded(self, *, p2p_online: bool) -> set[str]:
        out = set() if p2p_online else {BONSAI_MODEL}
        # A route the host's prober currently calls unhealthy is a wait, not a
        # choice. Drop it - unless dropping it would leave the policy nothing,
        # in which case a slow answer still beats no answer.
        sick = {m.name for m in self.config.catalog.all()
                if m.name not in out
                and latency.route_is_skippable(m.name, self.catalog_meta.get(m.name, {}))}
        if len(self.config.catalog.all()) - len(out | sick) >= latency.MIN_CANDIDATES:
            out |= sick
        return out

    def model_rows(self) -> list[dict]:
        """The whole catalog as the 'how it works' page shows it."""
        rows = []
        for m in self.config.catalog.all():
            rows.append({**self._model_basics(m),
                         "capability": {k: round(v, 1) for k, v in sorted(m.capability.items())},
                         "capabilityBasis": {k: m.evidence_basis(k) for k in sorted(m.capability)},
                         "evidence": m.evidence})
        return rows

    def _model_basics(self, m: ModelInfo) -> dict:
        demo = self.catalog_meta.get(m.name, {})
        return {
            "name": m.name,
            "label": demo.get("label", m.name),
            "org": demo.get("org", ""),
            "badge": demo.get("badge", ""),
            "note": demo.get("note", ""),
            "peerToPeer": bool(demo.get("peer_to_peer")),
            "baseline": bool(demo.get("baseline")),
            "executable": bool(demo.get("executable")),
            "benchId": m.bench_id,
            "contextTokens": m.context_tokens,
            "prices": {"input": m.prices.input, "output": m.prices.output,
                       "cacheRead": m.prices.cache_read, "cacheWrite": m.prices.cache_write},
            "cache": {"ttlSeconds": m.cache.ttl_seconds, "minTokens": m.cache.min_tokens,
                      "hitRate": m.cache.hit_rate},
            "evidenceStale": m.evidence_stale,
        }

    # -- deciding ----------------------------------------------------------
    def decide(self, conv: Conversation, messages: list[dict],
               classification: jev.Classification | None, classification_ms: float,
               now: float | None = None, *, exclude: set[str] | None = None) -> Decision:
        now = now or time.time()
        router = self.router
        ctx = self.context(exclude=exclude)
        cid = router.conversation_id(messages, None, None)
        raw_tokens = estimate_tokens(messages, None, None)
        prompt_tokens = router.estimator.estimate(raw_tokens)
        req = router._turn_request(classification, prompt_tokens, None, now, False, messages)
        # The published router prices a turn at up to 4000 output tokens. This
        # playground caps the answer far below that and, for an easy question,
        # gets a two-line one - so both halves of the objective are priced on
        # what this turn will actually produce. The length is the same for every
        # candidate, so it moves the absolute numbers, not the ranking; the time
        # half uses each route's own measured verbosity on top of it.
        req = replace(req, output_tokens=min(req.output_tokens,
                                             latency.DEFAULT_ANSWER_TOKENS[
                                                 latency.bucket_of(req.difficulty)]))
        choice = router.policy.choose(conv, req, ctx)
        model = ctx.catalog[choice.model]
        explanation = router.explain(ctx, conv, cid, model, choice.reason, req, classification,
                                     now, classification_ms, turn_start=True,
                                     switched_from=conv.current, prefix=cid)
        return Decision(model=model, request=req, classification=classification,
                        explanation=explanation, context=ctx, conversation_id=cid,
                        classification_ms=classification_ms, now=now, reason=choice.reason,
                        raw_tokens=raw_tokens,
                        reasoning=latency.plan_reasoning(
                            model.name, req.difficulty, self.catalog_meta.get(model.name, {})))

    def commit(self, conv: Conversation, decision: Decision, prompt_tokens: int,
               output_tokens: int, raw_estimate: int | None = None) -> None:
        """Record what happened, and let the token estimator learn from it."""
        if raw_estimate and prompt_tokens:
            self.router.estimator.observe(raw_estimate, prompt_tokens)
        conv.record_call(decision.model, prompt_tokens or decision.request.prompt_tokens,
                         output_tokens, decision.now)
        conv.turns += 1

    # -- presentation ------------------------------------------------------
    def candidate_rows(self, decision: Decision) -> list[dict]:
        rows = []
        chosen = decision.explanation.selection.selected
        for cand in decision.explanation.candidates:
            model = decision.context.catalog.get(cand.model)
            if model is None:
                continue
            row = self._model_basics(model)
            meta = self.catalog_meta.get(model.name, {})
            plan = latency.plan_reasoning(model.name, decision.request.difficulty, meta)
            row.update({
                "capabilityHere": round(cand.capability, 1),
                "capabilityBasis": cand.capability_basis,
                "evidenceStrength": cand.evidence_strength,
                "pSuccess": cand.p_success,
                "callUsd": cand.est_call_usd,
                "expectedUsd": cand.est_expected_usd,
                "warmTokens": cand.warm_tokens,
                "chosen": cand.model == chosen,
                # What the visitor waits for, next to what the turn costs.
                "expectedSeconds": round(
                    latency.expected_seconds(model, decision.request, meta, plan,
                                             output_tokens=SETTINGS.max_output_tokens), 1),
                "thinking": plan.level,
                # Whether this route's answer would be judged before the
                # visitor sees it. It belongs in the table because the
                # "Expected" column already contains the consequence: a route
                # whose failures are caught cheaply is priced as if they were.
                "checked": self.router is not None
                and self.router.policy.checks(model, decision.request, decision.context),
                "speedBasis": latency.BOOK.speed(
                    model.name, (meta.get("speed") or {}).get("health_key")).source,
            })
            rows.append(row)
        # Same order the policy ranks in: lowest expected cost first, and on a
        # tie the more capable route, so the table reads as the decision reads.
        rows.sort(key=lambda r: (r["expectedUsd"] is None,
                                 r["expectedUsd"] if r["expectedUsd"] is not None else 0.0,
                                 -r["capabilityHere"]))
        return rows

    def baseline_cost(self, decision: Decision, conv: Conversation) -> dict | None:
        """What this turn would have cost if every turn went to the frontier model."""
        if not self.baseline:
            return None
        model = decision.context.catalog.get(self.baseline)
        if model is None:
            return None
        cost = turn_call_cost(model, decision.request, conv.warm_tokens(model, decision.now),
                              decision.context)
        if not math.isfinite(cost):
            return None
        p = decision.context.success.p(model, decision.request.category, decision.request.difficulty)
        return {"model": model.name, "label": self.catalog_meta.get(model.name, {}).get("label", model.name),
                "callUsd": round(cost, 6), "pSuccess": round(p, 3)}

    def cache_curve(self, conv: Conversation, messages: list[dict],
                    classification: jev.Classification | None, pauses: list[int],
                    base_now: float, *, exclude: set[str] | None = None) -> list[dict]:
        """The same decision taken after different pauses, without changing state.

        This is the cache rule made visible: the only thing that differs between
        the rows is how much time passed, and therefore which routes are still
        warm.
        """
        out = []
        for pause in pauses:
            probe = copy.deepcopy(conv)
            decision = self.decide(probe, messages, classification, 0.0,
                                   now=base_now + pause, exclude=exclude)
            model = decision.model
            warm = probe.warm_tokens(model, decision.now)
            current = decision.context.catalog.get(conv.current)
            stay_cost = switch_cost = None
            if current is not None:
                stay_cost = turn_call_cost(current, decision.request,
                                           probe.warm_tokens(current, decision.now), decision.context)
                cold = turn_cost(current, decision.request.prompt_tokens, 0,
                                 decision.request.output_tokens)
                switch_cost = cold
            out.append({
                "pauseSeconds": pause,
                "selected": model.name,
                "label": self.catalog_meta.get(model.name, {}).get("label", model.name),
                "stayed": conv.current is not None and model.name == conv.current,
                "warmTokens": warm,
                "callUsd": _finite(turn_call_cost(model, decision.request, warm, decision.context)),
                "currentWarmUsd": _finite(stay_cost),
                "currentColdUsd": _finite(switch_cost),
                "reason": decision.reason,
            })
        return out


def _finite(value) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), 6)


ENGINE = Engine()


# ---------------------------------------------------------------------------
# what-if: the decision function, evaluated without sending anything anywhere
# ---------------------------------------------------------------------------
@dataclass
class WhatIf:
    """One hypothetical turn, priced on every route."""

    selected: str
    reason: str
    rows: list[dict]
    curve: list[dict]
    request: dict


def _warm_conversation(model_name: str, warm_tokens: int, now: float) -> Conversation:
    conv = Conversation()
    if model_name:
        conv.current = model_name
        conv.warm[model_name] = (warm_tokens, now)
        conv.turns = 2
    return conv


def what_if(engine: "Engine", *, prompt_tokens: int, output_tokens: int, category: str,
            difficulty: float, current: str | None, pauses: list[int],
            exclude: set[str] | None = None, steps: int = 1) -> WhatIf:
    """Price a hypothetical turn everywhere, then again after each pause.

    Nothing is sent to a model. This is the same policy object the live
    playground uses, asked a question instead of being given a request - which
    is the only honest way to show what a 120k-token prefix does to the
    decision without actually paying for one.
    """
    now = time.time()
    ctx = engine.context(exclude=exclude)
    base = TurnRequest(category=category, difficulty=difficulty, prompt_tokens=prompt_tokens,
                       output_tokens=output_tokens, now=now, steps=max(1, steps),
                       remaining_turns=3.0, stakes_usd=2.0, detect_prob=0.6)
    conv = _warm_conversation(current or "", prompt_tokens, now)
    choice = engine.router.policy.choose(conv, base, ctx)
    scored = engine.router.policy.evaluate(conv, base, ctx)

    current_model = ctx.catalog.get(current)
    rows = []
    for model, call, p, value in scored:
        warm = conv.warm_tokens(model, now)
        cold = turn_call_cost(model, base, 0, ctx)
        meta = engine.catalog_meta.get(model.name, {})
        plan = latency.plan_reasoning(model.name, difficulty, meta)
        rows.append({
            **engine._model_basics(model),
            "expectedSeconds": round(latency.expected_seconds(
                model, base, meta, plan, output_tokens=SETTINGS.max_output_tokens), 1),
            "thinking": plan.level,
            "capabilityHere": round(model.cap(category, evidence_discount=ctx.success.evidence_discount), 1),
            "pSuccess": round(p, 3),
            "warmTokens": warm,
            "warmUsd": _finite(call),
            "coldUsd": _finite(cold),
            "cacheSavesUsd": _finite(None if cold is None else max(0.0, (cold or 0) - (call or 0))),
            "expectedUsd": _finite(value),
            "chosen": model.name == choice.model,
            "isCurrent": model.name == current,
        })
    rows.sort(key=lambda r: (r["expectedUsd"] is None, r["expectedUsd"] or 0.0, -r["capabilityHere"]))

    curve = []
    for pause in pauses:
        probe = _warm_conversation(current or "", prompt_tokens, now)
        later = TurnRequest(**{**base.__dict__, "now": now + pause})
        pick = engine.router.policy.choose(probe, later, ctx)
        model = ctx.catalog[pick.model]
        warm = probe.warm_tokens(model, later.now)
        stay = (turn_call_cost(current_model, later, probe.warm_tokens(current_model, later.now), ctx)
                if current_model is not None else None)
        curve.append({
            "pauseSeconds": pause,
            "selected": model.name,
            "label": engine.catalog_meta.get(model.name, {}).get("label", model.name),
            "stayed": bool(current) and model.name == current,
            "warmTokens": warm,
            "callUsd": _finite(turn_call_cost(model, later, warm, ctx)),
            "currentUsd": _finite(stay),
            "currentWarm": bool(current_model is not None
                                and probe.warm_tokens(current_model, later.now) > 0),
            "reason": pick.reason,
        })

    return WhatIf(selected=choice.model, reason=choice.reason, rows=rows, curve=curve,
                  request={"promptTokens": prompt_tokens, "outputTokens": output_tokens,
                           "category": category, "difficulty": round(difficulty, 3),
                           "current": current, "steps": steps})
