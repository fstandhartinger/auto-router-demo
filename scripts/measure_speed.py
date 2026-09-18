#!/usr/bin/env python3
"""Refresh app/data/speed.measured.json against the live endpoints.

Five streamed calls per route - two easy, one medium, two hard, taken from the
demo's own examples - sent the way the demo sends them: thinking off where the
endpoint honours it, the smallest thinking it accepts otherwise, and room for
the answer on top of the thinking budget.

Three numbers come out of it per route: how long the first token takes, how
fast it decodes, and how long its *answer* is per difficulty bucket. The last
one is why this script exists. Pricing every route at the demo's output cap
made a one-line answer look like a twenty-seven second wait, and a route that
says "Canberra." lost a race to one that writes a paragraph about Sydney.

Where a route spends its whole budget thinking and never reaches the answer,
that bucket falls back to the default length rather than recording an answer of
zero tokens, which would advertise the route as instant.

    DEMO_ROUTES_FILE=routes.json python3 scripts/measure_speed.py
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import providers  # noqa: E402
from app.latency import DEFAULT_ANSWER_TOKENS, bucket_of  # noqa: E402
from app.settings import DATA, SETTINGS  # noqa: E402

BUCKET_DIFFICULTY = {"easy": 0.1, "medium": 0.5, "hard": 0.9}
#: Which of the demo's own examples stands in for each bucket.
PLAN = [("easy", "fact"), ("easy", "email"), ("medium", "refactor"),
        ("hard", "proof"), ("hard", "bug")]


def _prompts() -> dict[str, str]:
    return {e["id"]: e["prompt"] for e in json.loads((DATA / "examples.json").read_text())}


async def _one(route, client, prompt: str, bucket: str, meta: dict) -> dict:
    from app import latency

    plan = latency.plan_reasoning(route.model, BUCKET_DIFFICULTY[bucket], meta)
    budget = SETTINGS.max_output_tokens
    if plan.level not in ("off", "none"):
        budget += SETTINGS.reasoning_headroom_tokens
    started = time.perf_counter()
    first = None
    usage = providers.Usage()
    error = None
    async for kind, value in providers.stream_answer(
            route, [{"role": "user", "content": prompt}], budget, client,
            extra=dict(plan.request_extra or {})):
        if kind in ("delta", "reasoning") and first is None:
            first = time.perf_counter() - started
        elif kind == "usage":
            usage = value
        elif kind == "error":
            error = str(value)[:120]
    if error or first is None:
        return {"bucket": bucket, "err": error or "no tokens"}
    total = time.perf_counter() - started
    answer = max(0, usage.completion_tokens - usage.reasoning_tokens)
    return {"bucket": bucket, "ttft": round(first, 2), "total": round(total, 2),
            "out": usage.completion_tokens, "reasoning": usage.reasoning_tokens,
            "answer_tokens": answer,
            "tps": round(usage.completion_tokens / max(0.05, total - first), 1)}


async def _route(name: str, meta: dict, prompts: dict) -> tuple[str, list[dict]]:
    route = providers.route_for(name)
    if route is None or not route.usable:
        return name, []
    rows = []
    async with httpx.AsyncClient() as client:
        for bucket, example in PLAN:
            row = await _one(route, client, prompts[example], bucket, meta)
            rows.append(row)
            print(f"  {name:20s} {bucket:6s} {json.dumps(row)}", flush=True)
    return name, rows


async def main() -> None:
    from app.engine import ENGINE

    ENGINE.build()
    prompts = _prompts()
    names = sys.argv[1:] or [m.name for m in ENGINE.config.catalog.all()]
    results = await asyncio.gather(*[
        _route(n, ENGINE.catalog_meta.get(n, {}), prompts) for n in names])

    routes = {}
    for name, rows in results:
        answered = [r for r in rows if not r.get("err")]
        if not answered:
            print(f"  {name:20s} no call answered - left out", flush=True)
            continue
        entry = {
            "ttft_s": round(statistics.median(r["ttft"] for r in answered), 2),
            "decode_tps": round(statistics.median(r["tps"] for r in answered), 1),
            "reasoning_tokens": {}, "answer_tokens": {},
            "reasoning": any(r["reasoning"] for r in answered),
            "calls": len(rows), "answered": len(answered),
        }
        for bucket in DEFAULT_ANSWER_TOKENS:
            got = [r for r in answered if r["bucket"] == bucket]
            if not got:
                entry["answer_tokens"][bucket] = DEFAULT_ANSWER_TOKENS[bucket]
                continue
            entry["reasoning_tokens"][bucket] = int(
                statistics.median(r["reasoning"] for r in got))
            entry["answer_tokens"][bucket] = (
                DEFAULT_ANSWER_TOKENS[bucket] if any(r["answer_tokens"] == 0 for r in got)
                else int(statistics.median(r["answer_tokens"] for r in got)))
        routes[name] = entry

    path = DATA / "speed.measured.json"
    doc = json.loads(path.read_text())
    doc["measured_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    doc["answer_cap_tokens"] = SETTINGS.max_output_tokens
    doc["default_answer_tokens"] = DEFAULT_ANSWER_TOKENS
    doc["routes"] = {**doc.get("routes", {}), **routes}
    path.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"\nwrote {path} ({len(routes)} routes)")


if __name__ == "__main__":
    if not os.environ.get("DEMO_ROUTES_JSON") and not os.environ.get("DEMO_ROUTES_FILE"):
        sys.exit("set DEMO_ROUTES_JSON or DEMO_ROUTES_FILE first")
    asyncio.run(main())
