"""The classifier the demo routes on, with a labelled fallback.

First choice is Jev (TypeSafe's System One), which answers typed questions with
calibrated probabilities and is what the measured results were produced with.
If Jev is unreachable, a small free model is asked for the same fields as JSON
and the answer is wrapped in the same ``Classification`` object, so the policy
code below it cannot tell the difference - but the page always says which one
answered, because the fallback is not calibrated and its confidences are its
own opinion, not a measured probability.
"""

from __future__ import annotations

import asyncio
import json
import re

import httpx

from auto_router import jev

from . import providers

#: Which route answers when Jev does not. Free and small on purpose.
FALLBACK_MODELS = ("dsv4-flash", "glm-5.3-flash", "kimi-k3", "qwen3.8-27b")

CATEGORIES = tuple(jev.CATEGORY_OPTIONS)

PROMPT = """You classify one request for a model router. Answer with JSON only, no prose.

Fields:
  category: one of {categories}
  difficulty: 0.0 (greeting or one-line fact) to 1.0 (research-grade, only the strongest models succeed)
  needs_tools: 0.0-1.0 probability that handling it requires running code or calling tools
  needs_vision: 0.0-1.0 probability that an image must be looked at
  needs_long_context: 0.0-1.0 probability that more than about 50 pages must be read
  follow_up: 0.0-1.0 probability that it builds on an earlier turn
  stakes: 0.0-1.0 how costly a subtly wrong answer would be

Request:
<<<{request}>>>

Context: {context}

JSON:"""


def _num(value, default=0.5) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _parse(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _from_json(data: dict, model: str, latency_s: float) -> jev.Classification:
    category = str(data.get("category") or "general").strip().lower()
    if category not in CATEGORIES:
        category = "general"
    return jev.Classification(
        category=category,
        category_probs={category: 1.0},
        category_confidence=0.5,
        difficulty=_num(data.get("difficulty")),
        difficulty_confidence=0.5,
        needs_tools=_num(data.get("needs_tools"), 0.3),
        needs_vision=_num(data.get("needs_vision"), 0.0),
        needs_long_context=_num(data.get("needs_long_context"), 0.0),
        follow_up=_num(data.get("follow_up"), 0.3),
        stakes=_num(data.get("stakes")),
        latency_s=latency_s,
        model=model,
        raw={"_demo_classifier": {"kind": "fallback", "model": model}},
    )


UNAVAILABLE = {"kind": "unavailable", "label": "no classifier available"}


def classifier_meta(classification: jev.Classification | None) -> dict:
    """What the page must say about who classified this turn."""
    if classification is None:
        return UNAVAILABLE
    meta = (classification.raw or {}).get("_demo_classifier")
    if isinstance(meta, dict):
        return meta
    if classification.failed:
        return UNAVAILABLE
    return {"kind": "jev", "label": "Jev by TypeSafe AI",
            "model": classification.model or jev.MODEL}


async def _fallback(request: str, context: str, client: httpx.AsyncClient) -> jev.Classification | None:
    import time

    prompt = PROMPT.format(categories=", ".join(CATEGORIES),
                           request=jev.scrub(request, jev.REQUEST_CHARS),
                           context=jev.scrub(context, 400) or "(new conversation)")
    for name in FALLBACK_MODELS:
        route = providers.route_for(name)
        if route is None or not route.usable:
            continue
        started = time.perf_counter()
        text, _usage, error = await providers.complete(
            route, [{"role": "user", "content": prompt}], 300, client)
        if error or not text:
            continue
        data = _parse(text)
        if data is None:
            continue
        return _from_json(data, name, time.perf_counter() - started)
    return None


async def classify(request: str, context: str, client: httpx.AsyncClient) -> jev.Classification | None:
    """Jev first, a small free model second, ``None`` when neither answers."""
    result = await asyncio.to_thread(jev.classify, request, context)
    if not result.failed:
        return result
    return await _fallback(request, context, client)
