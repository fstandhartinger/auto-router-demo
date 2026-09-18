"""The demo's HTTP surface.

Three things happen here: the router is asked where a request should go, the
answer is streamed back from wherever that turned out to be, and everything the
router believed on the way is sent to the browser so the page can show the
decision instead of asserting it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from auto_router.economics import turn_cost

from . import classify, latency, providers
from .bonsai import MODEL_NAME as BONSAI_MODEL
from .bonsai import SWARM
from .engine import ENGINE, what_if
from .limits import LIMITER, Limiter, client_key
from auto_router.catalog import CATEGORIES

from .settings import DATA, SETTINGS

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

#: Offsets the multi-turn page can ask for, in seconds.
PAUSE_STEPS = [0, 60, 240, 290, 600, 1800, 3600]

log = logging.getLogger("demo")

#: The decision explorer costs nothing but CPU, so it gets its own, looser
#: bucket rather than eating a visitor's playground runs.
WHAT_IF_LIMITER = Limiter(per_ip_per_hour=600, global_per_day=200_000, daily_budget_usd=1e9)

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
    await app.state.client.aclose()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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


def _estimate(model, prompt_tokens: int, warm_tokens: int) -> float:
    cost = turn_cost(model, prompt_tokens, warm_tokens, SETTINGS.max_output_tokens)
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

    for alternative in _fallbacks(decision, conv, skip={chosen.name}):
        alternative.substituted = True
        alternative.note = (
            f"The router chose {ENGINE.catalog_meta.get(chosen.name, {}).get('label', chosen.name)}. "
            "This public demo only runs free and very cheap routes, so the answer below was "
            f"generated by {alternative.label}. The decision above is untouched.")
        return alternative
    return None


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
        if model is None:
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


def _decision_payload(decision, conv, execution: Execution | None, p2p) -> dict:
    record = decision.explanation.to_dict()
    baseline = ENGINE.baseline_cost(decision, conv)
    chosen_cost = next((c["callUsd"] for c in ENGINE.candidate_rows(decision) if c["chosen"]), None)
    saving = None
    if baseline and chosen_cost is not None:
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
        "p2p": p2p.to_dict(),
        "promptTokens": decision.request.prompt_tokens,
        "outputTokensAssumed": decision.request.output_tokens,
        "secondUsd": latency.SECOND_USD,
    }


#: How long a route may take to produce its first visible token before the demo
#: gives up on it and asks the policy for the next candidate.
FIRST_TOKEN_DEADLINE_S = float(SETTINGS.first_token_deadline_s)


async def _stream_answer(execution: Execution, messages: list[dict], emit, plan=None,
                         deadline: float | None = None):
    """Stream one answer, emitting SSE chunks through ``emit``. Returns usage.

    Reasoning is not forwarded token by token. A visitor asked a question; a
    wall of a model's private thinking is not an answer, and on the old build it
    was most of what the page showed. The browser gets one ``thinking`` event
    when it starts and one when it ends, with the count and the seconds, and
    draws a single line it can expand.
    """
    text = ""
    usage = providers.Usage()
    error = None
    started = time.perf_counter()
    first_token_at = None
    thinking_started = None
    thinking_chars = 0
    extra = dict(getattr(plan, "request_extra", None) or {})
    stream = providers.stream_answer(execution.route, messages, SETTINGS.max_output_tokens,
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
                        "chars": thinking_chars}))
            text += value
            await emit(_sse("delta", {"text": value}))
        elif kind == "reasoning":
            if thinking_started is None:
                thinking_started = time.perf_counter()
                await emit(_sse("thinking", {"model": execution.label}))
            thinking_chars += len(value)
        elif kind == "usage":
            usage = value
        elif kind == "error":
            error = value
            latency.BOOK.penalise(execution.model_name, str(value)[:120])
            await emit(_sse("answer_error", {"message": "the route did not answer"}))
    if first_token_at is not None and not error:
        latency.BOOK.forgive(execution.model_name)
    if thinking_started is not None and first_token_at is None:
        await emit(_sse("thinking_done", {
            "seconds": round(time.perf_counter() - thinking_started, 1),
            "chars": thinking_chars}))
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
    model = decision.context.catalog.get(model_name)
    if model is None or model.prices.is_free:
        return 0.0
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
        "limits": LIMITER.snapshot(client_key(_ip(request))),
        "p2p": status.to_dict(),
        "benchUrl": SETTINGS.bench_url,
        "repoUrl": SETTINGS.repo_url,
        "demoRepoUrl": SETTINGS.demo_repo_url,
        "benchErrors": ENGINE.bench_errors,
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
    key = client_key(_ip(request))
    if not WHAT_IF_LIMITER.check(key).allowed:
        return JSONResponse({"error": "per-ip"}, status_code=429)
    WHAT_IF_LIMITER.record_run(key)
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

    key = client_key(_ip(request))
    verdict = LIMITER.check(key)
    if not verdict.allowed:
        return JSONResponse({"error": verdict.reason, "retryAfter": verdict.retry_after_s,
                             "limits": LIMITER.snapshot(key)}, status_code=429)
    LIMITER.record_run(key)

    queue: asyncio.Queue = asyncio.Queue()

    async def emit(chunk: bytes) -> None:
        await queue.put(chunk)

    async def worker():
        from auto_router.policies import Conversation

        try:
            conv = Conversation()
            messages = [{"role": "user", "content": prompt}]
            await _run_turn(conv, messages, emit, key, session=None)
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
    key = client_key(_ip(request))
    verdict = LIMITER.check(key)
    if not verdict.allowed:
        return JSONResponse({"error": verdict.reason, "retryAfter": verdict.retry_after_s,
                             "limits": LIMITER.snapshot(key)}, status_code=429)
    LIMITER.record_run(key)

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
            await _run_turn(session.conversation, session.messages, emit, key, session=session)
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


async def _run_turn(conv, messages: list[dict], emit, key: str, session: Session | None) -> None:
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
            "message": "The demo has no route it may use for this request right now - "
                       "either today's budget is spent or no endpoint is configured."}))
        await emit(_sse("done", {"limits": LIMITER.snapshot(key), "costUsd": None}))
        return

    plan = decision.reasoning
    await emit(_sse("answering", {"model": execution.model_name, "label": execution.label,
                                  "substituted": execution.substituted, "note": execution.note,
                                  "thinking": getattr(plan, "level", "none"),
                                  "thinkingNote": getattr(plan, "note", "")}))
    usage, error, answer, timing = await _stream_answer(execution, messages, emit, plan)
    tried = {execution.model_name}
    # A route that has said nothing at all by its deadline is not answering this
    # turn. Take the next candidate by the same ranking rather than leaving the
    # visitor with a spinner.
    while timing is None and error:
        nxt = next(_fallbacks(decision, conv, skip=tried), None)
        if nxt is None:
            break
        tried.add(nxt.model_name)
        await emit(_sse("rerouted", {"from": execution.label, "to": nxt.label,
                                     "reason": error}))
        execution = nxt
        plan = latency.plan_reasoning(nxt.model_name, decision.request.difficulty,
                                      ENGINE.catalog_meta.get(nxt.model_name, {}))
        await emit(_sse("answering", {"model": nxt.model_name, "label": nxt.label,
                                      "substituted": True, "note": "",
                                      "thinking": plan.level, "thinkingNote": plan.note}))
        usage, error, answer, timing = await _stream_answer(execution, messages, emit, plan)
    if not error and not answer.strip():
        # A reasoning model can spend the whole capped budget thinking and never
        # start the answer. Say that, rather than showing an empty box.
        await emit(_sse("answer_error", {
            "message": f"{execution.label} used all {SETTINGS.max_output_tokens} tokens this demo "
                       "allows on its own reasoning and never got to the answer. That is a limit of "
                       "the demo's output cap, not of the routing decision above."}))
    cost = _actual_cost(execution.model_name, usage, decision)
    LIMITER.record_spend(cost)
    if session is not None:
        session.spent_usd += cost
        session.messages.append({"role": "assistant", "content": answer})
        session.turns.append({"model": execution.model_name, "usd": cost})
    ENGINE.commit(conv, decision, usage.prompt_tokens, usage.completion_tokens,
                  raw_estimate=decision.raw_tokens)
    await emit(_sse("done", {
        "limits": LIMITER.snapshot(key),
        "costUsd": round(cost, 6),
        "usage": {"promptTokens": usage.prompt_tokens, "completionTokens": usage.completion_tokens,
                  "cachedTokens": usage.cached_tokens},
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
# static site
# ---------------------------------------------------------------------------
app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

PAGES = {"": "index.html", "playground": "index.html", "cache": "index.html",
         "results": "index.html", "how": "index.html", "privacy": "index.html",
         "impressum": "index.html"}


@app.get("/{path:path}")
async def site(path: str):
    if path in PAGES:
        return FileResponse(STATIC / PAGES[path])
    candidate = (STATIC / path).resolve()
    if candidate.is_file() and STATIC in candidate.parents:
        return FileResponse(candidate)
    return FileResponse(STATIC / "index.html")
