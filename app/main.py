"""The demo's HTTP surface.

Three things happen here: the router is asked where a request should go, the
answer is streamed back from wherever that turned out to be, and everything the
router believed on the way is sent to the browser so the page can show the
decision instead of asserting it.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from auto_router.economics import turn_cost

from . import classify, latency, providers, verify
from . import meta as page_meta   # `meta` is already an endpoint name
from .bonsai import MODEL_NAME as BONSAI_MODEL
from .bonsai import SWARM
from .engine import ENGINE, what_if
from .limits import LIMITER, Limiter, client_key, subnet_key
from .usage import USAGE, classify_page_request
from auto_router.catalog import CATEGORIES

from .settings import DATA, SETTINGS

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

#: Offsets the multi-turn page can ask for, in seconds.
PAUSE_STEPS = [0, 60, 240, 290, 600, 1800, 3600]

log = logging.getLogger("demo")

#: The decision explorer costs nothing but CPU, so it gets its own, looser
#: bucket rather than eating a visitor's playground runs.
WHAT_IF_LIMITER = Limiter(per_ip_per_hour=600, per_ip_per_day=4000,
                          per_subnet_per_hour=2000, per_subnet_per_day=12_000,
                          global_per_day=200_000, daily_budget_usd=1e9)

app = FastAPI(title="Auto-router playground", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------
@dataclass
class Session:
    id: str
    conversation: object
    messages: list[dict] = field(default_factory=list)
    clock_offset: float = 0.0
    created: float = field(default_factory=time.time)
    seen: float = field(default_factory=time.time)
    spent_usd: float = 0.0
    turns: list[dict] = field(default_factory=list)


SESSIONS: dict[str, Session] = {}


def _new_session() -> Session:
    from auto_router.policies import Conversation

    if len(SESSIONS) >= SETTINGS.max_sessions:
        for key in sorted(SESSIONS, key=lambda k: SESSIONS[k].seen)[: SETTINGS.max_sessions // 4]:
            SESSIONS.pop(key, None)
    session = Session(id=uuid.uuid4().hex, conversation=Conversation())
    SESSIONS[session.id] = session
    return session


def _session(session_id: str | None) -> Session:
    session = SESSIONS.get(session_id or "")
    if session is None:
        return _new_session()
    session.seen = time.time()
    return session


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def _startup() -> None:
    app.state.client = httpx.AsyncClient(follow_redirects=False)
    # Building the catalog means asking the benchmark API about every model, so
    # it must not hold the server hostage: if that API is slow the process still
    # comes up, answers its health check, and says "still starting" until it is
    # ready. A retry loop handles a benchmark outage at boot.
    app.state.builder = asyncio.create_task(_build_catalog())


async def _build_catalog() -> None:
    for delay in (0, 15, 60, 300):
        if delay:
            await asyncio.sleep(delay)
        try:
            await asyncio.to_thread(ENGINE.build)
        except Exception:  # noqa: BLE001 - a bad boot must be retried, not fatal
            log.exception("building the catalog failed")
            continue
        if ENGINE.ready():
            await SWARM.status(app.state.client)
            return


@app.on_event("shutdown")
async def _shutdown() -> None:
    LIMITER.flush()
    USAGE.flush()
    await app.state.client.aclose()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _ip(request: Request) -> str:
    # A test key, set only on a deployment we are deliberately probing, lets us
    # verify the caps live from one machine without having to look like a
    # visitor. Without the key the header is ignored completely.
    if SETTINGS.test_key and request.headers.get("x-demo-test-key") == SETTINGS.test_key:
        pretend = request.headers.get("x-demo-test-ip", "").strip()
        if pretend:
            return pretend
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _buckets(request: Request) -> tuple[str, str]:
    """The two counters this request belongs to: its address and its network."""
    ip = _ip(request)
    return client_key(ip), subnet_key(ip)


def _sse(event: str, data) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


def _clean_prompt(text: str) -> str:
    return (text or "").strip()[: SETTINGS.max_prompt_chars]


async def _p2p_online() -> bool:
    status = await SWARM.status(app.state.client)
    return bool(status.online)


def _route_for(model_name: str):
    if model_name == BONSAI_MODEL:
        return SWARM.route()
    return providers.route_for(model_name)


@dataclass
class Execution:
    route: object
    model_name: str
    label: str
    est_usd: float
    substituted: bool = False
    note: str = ""
    #: Set when the router chose a frontier route: what it would have run, and
    #: what that answer was expected to cost. The page leads with it.
    frontier: dict | None = None


def _output_budget(plan) -> int:
    """The token budget one answer may use, thinking included.

    The answer cap is what the visitor is promised; the headroom on top is what
    keeps a route that ignores a reasoning-effort hint from spending the whole
    budget thinking and never reaching the answer.
    """
    if getattr(plan, "level", "none") in ("off", "none"):
        return SETTINGS.max_output_tokens
    return SETTINGS.max_output_tokens + SETTINGS.reasoning_headroom_tokens


def _estimate(model, prompt_tokens: int, warm_tokens: int) -> float:
    """The worst this call can cost: the answer cap plus the thinking headroom."""
    cost = turn_cost(model, prompt_tokens, warm_tokens,
                     SETTINGS.max_output_tokens + SETTINGS.reasoning_headroom_tokens)
    return 0.0 if not math.isfinite(cost) else cost


def _pick_execution(decision, conv) -> Execution | None:
    """Which route actually answers, and at what estimated cost.

    The routing decision is never changed by the demo's wallet. If the chosen
    model is too expensive to run here, or has no endpoint configured, the page
    says so and the answer comes from the best route the demo may actually use.
    """
    catalog = decision.context.catalog
    chosen = decision.model
    prompt_tokens = decision.request.prompt_tokens

    def candidate(model) -> Execution | None:
        if ENGINE.is_frontier(model.name):
            return None
        route = _route_for(model.name)
        if route is None or not route.usable:
            return None
        est = _estimate(model, prompt_tokens, conv.warm_tokens(model, decision.now))
        if not LIMITER.may_spend(est):
            return None
        return Execution(route=route, model_name=model.name,
                         label=ENGINE.catalog_meta.get(model.name, {}).get("label", model.name),
                         est_usd=round(est, 6))

    first = candidate(chosen)
    if first is not None:
        return first

    chosen_label = ENGINE.catalog_meta.get(chosen.name, {}).get("label", chosen.name)
    paused = LIMITER.paid_paused()
    frontier = _frontier_pick(decision) if ENGINE.is_frontier(chosen.name) else None
    for alternative in _fallbacks(decision, conv, skip={chosen.name}):
        alternative.substituted = True
        if frontier is not None:
            alternative.frontier = frontier
            alternative.note = (
                f"The router would send this to {chosen_label} (expected cost "
                f"{_usd(frontier['callUsd'])} for this answer). That is too expensive to run on "
                f"this free demo, so the answer below is from {alternative.label}, the best "
                f"affordable route. Run the router yourself and {chosen_label} answers it, "
                "on your own key.")
        elif paused:
            alternative.note = (
                f"The router chose {chosen_label}. Today's ${LIMITER.daily_budget_usd:.0f} demo "
                "budget for paid routes is used up, so the answer below came from the free route "
                f"{alternative.label}. The routing still runs and the decision above is untouched; "
                "the budget resets at midnight UTC.")
        else:
            alternative.note = (
                f"The router chose {chosen_label}. "
                "This public demo only runs free and very cheap routes, so the answer below was "
                f"generated by {alternative.label}. The decision above is untouched.")
        return alternative
    return None


def _usd(value) -> str:
    if value is None:
        return "unknown"
    return f"${value:.4f}" if value < 0.01 else f"${value:.3f}"


def _frontier_pick(decision) -> dict:
    """The frontier route the router chose, and what its answer would cost."""
    chosen = decision.model
    row = next((r for r in ENGINE.candidate_rows(decision) if r["name"] == chosen.name), {})
    return {"model": chosen.name,
            "label": ENGINE.catalog_meta.get(chosen.name, {}).get("label", chosen.name),
            "org": ENGINE.catalog_meta.get(chosen.name, {}).get("org", ""),
            "callUsd": row.get("callUsd"), "expectedUsd": row.get("expectedUsd"),
            "pSuccess": row.get("pSuccess")}


def _fallbacks(decision, conv, skip: set[str]):
    """The next routes to try, in the order the policy itself ranks them.

    Used when a route has already been given its deadline and produced nothing.
    Ranking by the same expected score - which now includes time - rather than
    by raw capability, so the replacement for a route that stalled is not
    chosen to be the slowest remaining one.
    """
    ranked = [row["name"] for row in ENGINE.candidate_rows(decision)]
    # Only the routes the policy actually scored are ranked. Anything it never
    # priced - filtered out for context length, tools or vision - still belongs
    # at the back of the queue: an answer from a route the policy did not rank
    # beats telling the visitor the demo has nowhere to send this.
    rest = sorted((m.name for m in decision.context.catalog.all() if m.name not in ranked),
                  key=lambda n: -decision.context.catalog[n].cap(decision.request.category))
    for name in [n for n in ranked + rest if n not in skip]:
        model = decision.context.catalog.get(name)
        if model is None or ENGINE.is_frontier(name):
            continue
        route = _route_for(name)
        if route is None or not route.usable:
            continue
        est = _estimate(model, decision.request.prompt_tokens,
                        conv.warm_tokens(model, decision.now))
        if not LIMITER.may_spend(est):
            continue
        yield Execution(route=route, model_name=name,
                        label=ENGINE.catalog_meta.get(name, {}).get("label", name),
                        est_usd=round(est, 6))


def _escalations(decision, conv, failed_name: str, skip: set[str]):
    """Routes for a second attempt after the judge rejected the first answer.

    The same ranking ``_fallbacks`` uses - the policy's own expected score,
    money and seconds - filtered to routes that are *meaningfully* stronger
    than the one that failed. Without that bar a catalog of similar free routes
    would answer a rejected answer with a route of the same tier, which is not
    an escalation; the bar is the published router's
    (``VerifyPolicy.min_capability_gain``).
    """
    catalog = decision.context.catalog
    failed = catalog.get(failed_name)
    if failed is None:
        return
    category = decision.request.category
    bar = failed.cap(category) + ENGINE.verify_policy.min_capability_gain
    for execution in _fallbacks(decision, conv, skip=skip):
        model = catalog.get(execution.model_name)
        if model is not None and model.cap(category) >= bar:
            yield execution


def _decision_payload(decision, conv, execution: Execution | None, p2p) -> dict:
    record = decision.explanation.to_dict()
    baseline = ENGINE.baseline_cost(decision, conv)
    chosen_cost = next((c["callUsd"] for c in ENGINE.candidate_rows(decision) if c["chosen"]), None)
    saving = None
    # A saving only exists when the pick is cheaper than the baseline; a
    # frontier pick above it (Fable, Astra) must not read as "0.5x cheaper".
    if baseline and chosen_cost is not None and chosen_cost < baseline["callUsd"]:
        saving = {"baseline": baseline, "chosenUsd": chosen_cost,
                  "savedUsd": round(max(0.0, baseline["callUsd"] - chosen_cost), 6),
                  "factor": (round(baseline["callUsd"] / chosen_cost, 1)
                             if chosen_cost > 0 else None)}
    return {
        "classification": record["classification"],
        "classifier": classify.classifier_meta(decision.classification),
        "categoryProbabilities": (decision.classification.category_probs
                                  if decision.classification else {}),
        "selection": record["selection"],
        "cache": record["cache"],
        "estimated": record["estimated_outcome"],
        "notes": record["notes"],
        "candidates": ENGINE.candidate_rows(decision),
        "saving": saving,
        "execution": ({"model": execution.model_name, "label": execution.label,
                       "substituted": execution.substituted, "note": execution.note,
                       "estimatedUsd": execution.est_usd} if execution else None),
        # The router picked a frontier route: say so first, whether or not an
        # affordable route could answer instead.
        "frontier": ((execution.frontier if execution and execution.frontier else None)
                     or (_frontier_pick(decision) if ENGINE.is_frontier(decision.model.name)
                         else None)),
        "p2p": p2p.to_dict(),
        "promptTokens": decision.request.prompt_tokens,
        "outputTokensAssumed": decision.request.output_tokens,
        "secondUsd": latency.SECOND_USD,
    }


#: How long a route may take to produce its first visible token before the demo
#: gives up on it and asks the policy for the next candidate.
FIRST_TOKEN_DEADLINE_S = float(SETTINGS.first_token_deadline_s)

#: How much of a model's thinking is forwarded to the browser. Past this the
#: block says it was cut; nobody reads 60k characters of a model muttering, and
#: it is the visitor's bandwidth.
MAX_REASONING_CHARS = 12_000


async def _stream_answer(execution: Execution, messages: list[dict], emit, plan=None,
                         deadline: float | None = None):
    """Stream one answer, emitting SSE chunks through ``emit``. Returns usage.

    Reasoning is forwarded, but into its own place: a ``thinking`` event opens
    a collapsed block with a running clock, ``reasoning`` events fill it, and
    ``thinking_done`` closes it with the seconds and the size. A wall of a
    model's private thinking is not an answer and must never be the page's main
    column - but hiding it entirely threw away the part of a reasoning model a
    reader most wants to look at.
    """
    text = ""
    usage = providers.Usage()
    error = None
    started = time.perf_counter()
    first_token_at = None
    thinking_started = None
    thinking_chars = 0
    extra = dict(getattr(plan, "request_extra", None) or {})
    stream = providers.stream_answer(execution.route, messages, _output_budget(plan),
                                     app.state.client, extra=extra)
    limit = deadline if deadline is not None else FIRST_TOKEN_DEADLINE_S
    while True:
        try:
            # Only the wait for the *first* visible token is bounded. Once the
            # answer is flowing the visitor can see progress and stopping would
            # be worse than waiting.
            budget = None if first_token_at is not None else max(
                0.1, limit - (time.perf_counter() - started))
            kind, value = await asyncio.wait_for(stream.__anext__(), timeout=budget)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            await stream.aclose()
            latency.BOOK.penalise(execution.model_name, f"no first token within {limit:.0f}s")
            return usage, f"no first token within {limit:.0f}s", text, None
        if kind == "delta":
            if first_token_at is None:
                first_token_at = time.perf_counter() - started
                if thinking_started is not None:
                    await emit(_sse("thinking_done", {
                        "seconds": round(time.perf_counter() - thinking_started, 1),
                        "chars": thinking_chars,
                        "truncated": thinking_chars > MAX_REASONING_CHARS}))
            text += value
            await emit(_sse("delta", {"text": value}))
        elif kind == "reasoning":
            if thinking_started is None:
                thinking_started = time.perf_counter()
                await emit(_sse("thinking", {"model": execution.label}))
            thinking_chars += len(value)
            if thinking_chars <= MAX_REASONING_CHARS:
                await emit(_sse("reasoning", {"text": value}))
        elif kind == "usage":
            usage = value
        elif kind == "error":
            error = value
            latency.BOOK.penalise(execution.model_name, str(value)[:120])
            # No notice here: the caller still has the next candidate to try,
            # and an error box that is replaced by an answer a second later
            # reads as if the demo were broken when it is doing its job.
    if first_token_at is not None and not error:
        latency.BOOK.forgive(execution.model_name)
    if thinking_started is not None and first_token_at is None:
        await emit(_sse("thinking_done", {
            "seconds": round(time.perf_counter() - thinking_started, 1),
            "chars": thinking_chars,
            "truncated": thinking_chars > MAX_REASONING_CHARS}))
    # Feed the demo's own timings back into the route's speed estimate, which is
    # what the next decision reads.
    total = time.perf_counter() - started
    if first_token_at and usage.completion_tokens:
        latency.BOOK.observe(execution.model_name, first_token_at,
                             usage.completion_tokens / max(total - first_token_at, 1e-3),
                             usage.completion_tokens)
    return usage, error, text, {"firstTokenS": round(first_token_at, 2) if first_token_at else None,
                                "totalS": round(total, 2),
                                "thinkingChars": thinking_chars}


def _actual_cost(model_name: str, usage: providers.Usage, decision) -> float:
    """What this call cost, from the provider's own accounting where it gives it.

    A reported cost of exactly zero for a metered model is not an accounting,
    it is a gap in one - OpenRouter returns ``cost: 0`` for the GPT-5.6 routes
    while publishing $2 and $10 per million for them. Believing it would let
    the most expensive half of the catalog run outside the daily cap, so a zero
    falls back to list-price arithmetic.
    """
    model = decision.context.catalog.get(model_name)
    if model is None or model.prices.is_free:
        return 0.0
    if usage.cost_usd is not None and usage.cost_usd > 0:
        return usage.cost_usd
    prompt = usage.prompt_tokens or decision.request.prompt_tokens
    cached = min(usage.cached_tokens, prompt)
    cost = (cached * model.prices.read + (prompt - cached) * model.prices.write
            + usage.completion_tokens * model.prices.output) / 1e6
    return max(0.0, cost)


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    """The process is alive. ``ready`` says whether the catalog is built yet."""
    return {"ok": True, "ready": ENGINE.ready(),
            "models": len(ENGINE.config.catalog.all()) if ENGINE.ready() else 0}


@app.get("/api/meta")
async def meta(request: Request):
    status = await SWARM.status(app.state.client)
    return {
        "examples": json.loads((DATA / "examples.json").read_text()),
        "models": ENGINE.model_rows() if ENGINE.ready() else [],
        "limits": LIMITER.snapshot(*_buckets(request)),
        "p2p": status.to_dict(),
        "benchUrl": SETTINGS.bench_url,
        "repoUrl": SETTINGS.repo_url,
        "demoRepoUrl": SETTINGS.demo_repo_url,
        "benchErrors": ENGINE.bench_errors,
        # Only set once there is a documented, permitted way to use a flat-rate
        # plan from the router; the page stays silent about it until then.
        "subscriptionUrl": SETTINGS.subscription_url or None,
        "pauseSteps": PAUSE_STEPS,
        "secondUsd": latency.SECOND_USD,
        "measuredSpeed": latency.BOOK.snapshot(),
        "ready": ENGINE.ready(),
    }


SCENARIO = json.loads((DATA / "scenario.json").read_text())


def _seed_scenario(session: Session) -> None:
    """Start the conversation on a pasted document.

    Without it every demo prompt is a few dozen tokens, which is below every
    provider's minimum cacheable prefix - so there would be no cache to reason
    about and the page would be a lie.
    """
    session.messages = [
        {"role": "user", "content": SCENARIO["intro"] + SCENARIO["document"] + SCENARIO["outro"]},
        {"role": "assistant", "content": SCENARIO["ack"]},
    ]


@app.get("/api/scenario")
async def scenario():
    return {"name": SCENARIO["name"], "about": SCENARIO["about"],
            "suggestions": SCENARIO["suggestions"],
            "approxTokens": len(SCENARIO["document"]) // 4,
            "preview": SCENARIO["document"][:700]}


@app.post("/api/what-if")
async def what_if_route(request: Request):
    """Ask the policy what it would do, without sending anything to a model."""
    if not ENGINE.ready():
        return JSONResponse({"error": "not ready"}, status_code=503)
    key, subnet = _buckets(request)
    if not WHAT_IF_LIMITER.check(key, subnet).allowed:
        return JSONResponse({"error": "per-ip"}, status_code=429)
    WHAT_IF_LIMITER.record_run(key, subnet)
    body = await request.json()
    catalog_names = {m.name for m in ENGINE.config.catalog.all()}
    current = body.get("current") if body.get("current") in catalog_names else None
    free_excluded = set()
    if body.get("excludeFree"):
        free_excluded = {m.name for m in ENGINE.config.catalog.all()
                         if m.prices.is_free and not ENGINE.catalog_meta.get(m.name, {}).get("peer_to_peer")}
        if current in free_excluded:
            current = None
    p2p = await SWARM.status(app.state.client)
    exclude = ENGINE.excluded(p2p_online=p2p.online) | free_excluded
    if current in exclude:
        current = None
    result = await asyncio.to_thread(
        what_if, ENGINE,
        prompt_tokens=max(200, min(int(body.get("promptTokens") or 20000), 400_000)),
        output_tokens=max(100, min(int(body.get("outputTokens") or 1500), 16_000)),
        category=(body.get("category") if body.get("category") in CATEGORIES else "coding"),
        difficulty=max(0.0, min(float(body.get("difficulty") or 0.5), 1.0)),
        current=current,
        pauses=PAUSE_STEPS,
        exclude=exclude,
        steps=max(1, min(int(body.get("steps") or 1), 20)),
    )
    return {"selected": result.selected, "reason": result.reason, "rows": result.rows,
            "curve": result.curve, "request": result.request,
            "excludedFree": sorted(free_excluded)}


@app.get("/api/results")
async def results():
    return JSONResponse(json.loads((DATA / "results.json").read_text()))


@app.post("/api/run")
async def run(request: Request):
    """The playground: classify, decide, answer. One server-sent-event stream."""
    body = await request.json()
    prompt = _clean_prompt(body.get("prompt", ""))
    if not prompt:
        return JSONResponse({"error": "empty prompt"}, status_code=400)
    if not ENGINE.ready():
        return JSONResponse({"error": "starting"}, status_code=503)

    key, subnet = _buckets(request)
    verdict = LIMITER.check(key, subnet)
    if not verdict.allowed:
        return JSONResponse({"error": verdict.reason, "retryAfter": verdict.retry_after_s,
                             "limits": LIMITER.snapshot(key, subnet)}, status_code=429)
    LIMITER.record_run(key, subnet)
    USAGE.event("runs")

    queue: asyncio.Queue = asyncio.Queue()

    async def emit(chunk: bytes) -> None:
        await queue.put(chunk)

    async def worker():
        from auto_router.policies import Conversation

        try:
            conv = Conversation()
            messages = [{"role": "user", "content": prompt}]
            await _run_turn(conv, messages, emit, (key, subnet), session=None)
        except Exception as exc:  # noqa: BLE001 - the stream must always end
            log.exception("playground run failed")
            await queue.put(_sse("fatal", {"message": type(exc).__name__}))
        finally:
            await queue.put(None)

    task = asyncio.create_task(worker())

    async def body_stream():
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            task.cancel()

    return StreamingResponse(body_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.post("/api/chat")
async def chat(request: Request):
    """The multi-turn page: the same router, one conversation, a movable clock."""
    body = await request.json()
    prompt = _clean_prompt(body.get("prompt", ""))
    if not prompt:
        return JSONResponse({"error": "empty prompt"}, status_code=400)
    if not ENGINE.ready():
        return JSONResponse({"error": "starting"}, status_code=503)
    key, subnet = _buckets(request)
    verdict = LIMITER.check(key, subnet)
    if not verdict.allowed:
        return JSONResponse({"error": verdict.reason, "retryAfter": verdict.retry_after_s,
                             "limits": LIMITER.snapshot(key, subnet)}, status_code=429)
    LIMITER.record_run(key, subnet)
    USAGE.event("chats")

    session = _session(body.get("session"))
    if not session.messages:
        _seed_scenario(session)
    if session.messages[-1].get("role") == "user":
        # The previous turn was abandoned (the browser went away mid-stream), so
        # its question never got an answer. Drop it rather than send two user
        # messages in a row.
        session.messages.pop()
    pause = int(body.get("pauseSeconds") or 0)
    pause = max(0, min(pause, 24 * 3600))
    session.clock_offset += pause
    if len(session.messages) >= (SETTINGS.max_chat_turns + 1) * 2:
        # The pasted document stays: it is the prefix the whole page is about.
        session.messages = session.messages[:2] + session.messages[-(SETTINGS.max_chat_turns - 1) * 2:]
    session.messages.append({"role": "user", "content": prompt})

    queue: asyncio.Queue = asyncio.Queue()

    async def emit(chunk: bytes) -> None:
        await queue.put(chunk)

    async def worker():
        try:
            await _run_turn(session.conversation, session.messages, emit, (key, subnet), session=session)
        except Exception as exc:  # noqa: BLE001
            log.exception("chat turn failed")
            await queue.put(_sse("fatal", {"message": type(exc).__name__}))
        finally:
            await queue.put(None)

    task = asyncio.create_task(worker())

    async def body_stream():
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            task.cancel()

    return StreamingResponse(body_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _deadline_for(execution: Execution) -> float:
    """This route's own first-token deadline, from what it was measured to do."""
    return latency.first_token_deadline(execution.model_name,
                                        ENGINE.catalog_meta.get(execution.model_name, {}),
                                        FIRST_TOKEN_DEADLINE_S)


def _plan_for(execution: Execution, decision) -> latency.ReasoningPlan:
    """How hard the route that is about to answer should think about this turn."""
    return latency.plan_reasoning(execution.model_name, decision.request.difficulty,
                                  ENGINE.catalog_meta.get(execution.model_name, {}))


async def _run_turn(conv, messages: list[dict], emit, keys: tuple[str, str],
                    session: Session | None) -> None:
    prompt = messages[-1]["content"]
    context = ""
    if len(messages) > 1:
        previous = [m for m in messages[:-1] if m.get("role") == "user"]
        context = (f"{len(messages)} messages so far. Previous user request: "
                   f"{(previous[-1]['content'] if previous else '')[:400]}")

    await emit(_sse("classifying", {"classifier": "Jev by TypeSafe AI"}))
    started = time.perf_counter()
    classification = await classify.classify(prompt, context, app.state.client)
    classification_ms = (time.perf_counter() - started) * 1000

    p2p = await SWARM.status(app.state.client)
    exclude = ENGINE.excluded(p2p_online=p2p.online)
    now = time.time() + (session.clock_offset if session else 0.0)
    decision = await asyncio.to_thread(ENGINE.decide, conv, messages, classification,
                                       classification_ms, now, exclude=exclude)
    execution = _pick_execution(decision, conv)
    if ENGINE.is_frontier(decision.model.name):
        USAGE.event("frontierPicks")
    payload = _decision_payload(decision, conv, execution, p2p)
    if session is not None:
        payload["session"] = session.id
        payload["clockOffset"] = session.clock_offset
        payload["turn"] = len(session.turns) + 1
        payload["cacheCurve"] = await asyncio.to_thread(
            ENGINE.cache_curve, conv, messages, classification, PAUSE_STEPS, time.time(),
            exclude=exclude)
    await emit(_sse("decision", payload))

    if execution is None:
        await emit(_sse("answer_error", {
            "message": ("Today's demo budget for paid routes is spent and no free route can take "
                        "this request, so there is no answer below - the routing above still ran "
                        "and costs nothing. The budget resets at midnight UTC.")
                       if LIMITER.paid_paused() else
                       "The demo has no route it may use for this request right now - "
                       "no endpoint is configured for any candidate."}))
        await emit(_sse("done", {"limits": LIMITER.snapshot(*keys), "costUsd": None}))
        return

    # The plan has to follow the route that actually answers. When the demo
    # substitutes a cheaper route for the one the router chose, the chosen
    # route's dialect means nothing to it - and sending the wrong one reads as
    # "thinking off" while the model thinks at full length.
    plan = _plan_for(execution, decision)
    await emit(_sse("answering", {"model": execution.model_name, "label": execution.label,
                                  "substituted": execution.substituted, "note": execution.note,
                                  "thinking": getattr(plan, "level", "none"),
                                  "thinkingNote": getattr(plan, "note", "")}))
    usage, error, answer, timing = await _stream_answer(execution, messages, emit, plan,
                                                        deadline=_deadline_for(execution))
    tried = {execution.model_name}
    # Whatever the turn cost on the way to an answer, including the attempts
    # that produced nothing. Only the last call used to be counted, so a route
    # that thought for two cents and then stopped was free as far as the daily
    # budget was concerned.
    spent = _actual_cost(execution.model_name, usage, decision)
    # A route that produced no answer is not answering this turn, however it
    # failed: it went quiet until its deadline, the endpoint refused outright -
    # a free public endpoint hitting its shared upstream rate limit is the
    # common case - or it spent the whole budget thinking and stopped. Take the
    # next candidate by the same ranking rather than leaving the visitor with
    # an apology.
    while not answer.strip():
        nxt = next(_fallbacks(decision, conv, skip=tried), None)
        if nxt is None:
            break
        tried.add(nxt.model_name)
        await emit(_sse("rerouted", {"from": execution.label, "to": nxt.label,
                                     "reason": error or (
                                         f"spent all {_output_budget(plan)} tokens thinking"
                                         if usage.reasoning_tokens else
                                         "the route sent nothing")}))
        execution = nxt
        plan = _plan_for(execution, decision)
        await emit(_sse("answering", {"model": nxt.model_name, "label": nxt.label,
                                      "substituted": True, "note": "",
                                      "thinking": plan.level, "thinkingNote": plan.note}))
        usage, error, answer, timing = await _stream_answer(execution, messages, emit, plan,
                                                            deadline=_deadline_for(execution))
        spent += _actual_cost(execution.model_name, usage, decision)
    if not answer.strip():
        # Every route the demo may use is exhausted, or the one that answered
        # spent its whole capped budget thinking and never started. Say which,
        # rather than showing an empty box.
        if error:
            message = (f"No route this demo may use answered this request: {error}. The routing "
                       "above still ran and costs nothing.")
        elif usage.reasoning_tokens:
            message = (f"{execution.label} used all {_output_budget(plan)} tokens this demo "
                       "allows on its own reasoning and never got to the answer. That is a limit "
                       "of the demo's output cap, not of the routing decision above.")
        else:
            message = (f"{execution.label} closed the connection without sending anything. "
                       "The routing above still ran and costs nothing.")
        await emit(_sse("answer_error", {"message": message}))
    # ------------------------------------------------------------------
    # Check the answer - but only a cheap one. The gate is the published
    # router's (auto_router/verify.py): a frontier route is not checked,
    # because Jev is not stronger than one and would produce false alarms
    # rather than quality.
    # ------------------------------------------------------------------
    catalog = decision.context.catalog
    answered = catalog.get(execution.model_name)
    check = await verify.check(
        ENGINE.verify_policy, answered, decision.request.category,
        request=prompt, answer=answer, label=execution.label,
        evidence_discount=decision.context.success.evidence_discount,
        needs_long_context=decision.request.needs_long_context) if answered is not None else None
    escalated_to = None
    if check is not None and check.escalate:
        target = next(_escalations(decision, conv, execution.model_name, skip=tried), None)
        if target is not None:
            escalated_to = target.label
    if check is not None:
        await emit(_sse("verify", {**check.payload(), "escalatedTo": escalated_to,
                                   "chip": verify.chip_text(check, escalated_to)}))
    if escalated_to is not None:
        # The conversation has now proved it is harder than it was routed for.
        # Raising the floor is what stops the next turn from starting on the
        # same too-cheap route and paying for the same failure again.
        conv.floor = max(conv.floor, ENGINE.verify_policy.difficulty_floor)
        conv.floor_set_at = now
        first_answer, first_label = answer, execution.label
        execution = target
        tried.add(target.model_name)
        await emit(_sse("escalated", {"from": first_label, "to": target.label,
                                      "failure": verify.FAILURE_WORDS.get(check.failure, ""),
                                      "p": round(check.p_adequate or 0.0, 2),
                                      "firstAnswer": first_answer}))
        plan = _plan_for(execution, decision)
        await emit(_sse("answering", {"model": execution.model_name, "label": execution.label,
                                      "substituted": False, "note": "",
                                      "thinking": getattr(plan, "level", "none"),
                                      "thinkingNote": getattr(plan, "note", "")}))
        usage2, error2, answer2, timing = await _stream_answer(
            execution, messages, emit, plan, deadline=_deadline_for(execution))
        spent += _actual_cost(execution.model_name, usage2, decision)
        if answer2.strip():
            answer, usage, error = answer2, usage2, error2
        else:
            await emit(_sse("answer_error", {
                "message": (f"{execution.label} did not answer the second attempt, so the first "
                            "answer above stands.")}))

    cost = spent
    LIMITER.record_spend(cost)
    USAGE.spend(cost)
    if session is not None:
        session.spent_usd += cost
        session.messages.append({"role": "assistant", "content": answer})
        session.turns.append({"model": execution.model_name, "usd": cost})
    ENGINE.commit(conv, decision, usage.prompt_tokens, usage.completion_tokens,
                  raw_estimate=decision.raw_tokens)
    await emit(_sse("done", {
        "limits": LIMITER.snapshot(*keys),
        "costUsd": round(cost, 6),
        "usage": {"promptTokens": usage.prompt_tokens, "completionTokens": usage.completion_tokens,
                  "cachedTokens": usage.cached_tokens,
                  "reasoningTokens": usage.reasoning_tokens,
                  "costMetered": usage.cost_usd is not None},
        "timing": timing,
        "error": error is not None,
    }))


@app.post("/api/session/reset")
async def reset_session(request: Request):
    body = await request.json()
    SESSIONS.pop(body.get("session") or "", None)
    session = _new_session()
    _seed_scenario(session)
    return {"session": session.id}


# ---------------------------------------------------------------------------
# launch statistics (token-protected; aggregate daily totals only)
# ---------------------------------------------------------------------------
LAUNCH_STATS_FILE = Path(SETTINGS.state_file).with_name("launch-stats.json")
MAX_LAUNCH_STATS_BYTES = 512_000


def _authorised(request: Request) -> bool:
    token = SETTINGS.stats_token
    if not token:
        return False
    header = request.headers.get("authorization", "")
    offered = header[7:].strip() if header.lower().startswith("bearer ") else ""
    return bool(offered) and hmac.compare_digest(offered.encode(), token.encode())


def _denied() -> JSONResponse:
    # The same answer whether the token is wrong or not configured at all.
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/usage")
async def usage_snapshot(request: Request, days: int = 30):
    """Exact per-day counters of this process, for the host collector."""
    if not _authorised(request):
        return _denied()
    return USAGE.snapshot(max(1, min(days, 400)))


@app.get("/api/launch-stats")
async def launch_stats(request: Request):
    """The launch dashboard: this site's live totals plus the collector's snapshot."""
    if not _authorised(request):
        return _denied()
    collected = None
    if LAUNCH_STATS_FILE.exists():
        try:
            collected = json.loads(LAUNCH_STATS_FILE.read_text())
        except (OSError, ValueError):
            collected = None
    return {"live": USAGE.report(30), "collected": collected,
            "budget": {"dailyUsd": LIMITER.daily_budget_usd,
                       "leftTodayUsd": round(LIMITER.budget_left(), 4)}}


@app.post("/api/launch-stats")
async def store_launch_stats(request: Request):
    """The host collector pushes its merged snapshot here, so the page can show it."""
    if not _authorised(request):
        return _denied()
    raw = await request.body()
    if len(raw) > MAX_LAUNCH_STATS_BYTES:
        return JSONResponse({"error": "too large"}, status_code=413)
    try:
        data = json.loads(raw)
    except ValueError:
        return JSONResponse({"error": "not json"}, status_code=400)
    LAUNCH_STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAUNCH_STATS_FILE.write_text(json.dumps(data))
    return {"ok": True}


@app.get("/install.sh")
async def install_script():
    """The installer, counted: one download is one ``installs`` tick, whoever asks."""
    USAGE.event("installs")
    return FileResponse(STATIC / "install.sh", media_type="text/x-shellscript",
                        headers={"Cache-Control": "no-store"})


@app.get("/stats")
async def stats_page():
    # The page is public; the numbers need the token, which the page reads from
    # the URL fragment (never sent to a server, never in an access log).
    return FileResponse(STATIC / "stats.html", headers={"Cache-Control": "no-store",
                                                        "X-Robots-Tag": "noindex"})


# ---------------------------------------------------------------------------
# static site
# ---------------------------------------------------------------------------
app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

PAGES = {"": "index.html", "playground": "index.html", "cache": "index.html",
         "results": "index.html", "how": "index.html", "run": "index.html",
         "privacy": "index.html", "impressum": "index.html"}

#: Read once. Every page is this template with its own head block substituted
#: in (app/meta.py): a crawler never runs the router that would otherwise
#: decide which page it is looking at.
TEMPLATE = (STATIC / "index.html").read_text()


def _page(path: str) -> HTMLResponse:
    return HTMLResponse(page_meta.render(TEMPLATE, path))


@app.get("/{path:path}")
async def site(path: str, request: Request):
    if path in PAGES:
        USAGE.page(classify_page_request(request.method, path, request.headers))
        return _page("" if path == "playground" else path)
    candidate = (STATIC / path).resolve()
    if candidate.is_file() and STATIC in candidate.parents:
        return FileResponse(candidate)
    # An unknown path still renders the app, which shows its own not-found
    # view; the preview it gets is the site's, not a stale page's.
    return _page("")
