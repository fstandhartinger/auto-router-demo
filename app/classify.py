"""The classifier the demo routes on, with labelled fallbacks.

First choice is s1-llm-auto-router, Decision Models' open (MIT) routing model,
run on this server's CPU: no request leaves the container for classification.
If it cannot answer, hosted Jev (TypeSafe's System One) is asked, and if that is
unreachable too a small free model is asked for the same fields as JSON. Every
answer is wrapped in the same ``Classification`` object, so the policy code
below cannot tell the difference - but the page always says which one answered,
because the free-model fallback is not calibrated and its confidences are its
own opinion, not a measured probability.

Difficulty is on the router's own 0..1 scale (expected level / 4), which is
what s1-llm-auto-router answers natively. Jev answers on a compressed scale, so
its difficulty is mapped back here before it reaches the policy.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import re

import httpx

from auto_router import jev, s1_router

from . import providers

log = logging.getLogger("demo.classify")

#: Jev reports difficulty on a compressed scale: ``d_jev = 0.27 + 0.51 x level``.
#: Measured on the 78-task set (EXPERIMENTS.md section 4).
JEV_CALIBRATION = (0.27, 0.51)

#: ``DEMO_CLASSIFIER=jev`` switches the first choice back to hosted Jev.
FIRST_CHOICE = (os.environ.get("DEMO_CLASSIFIER") or "s1").strip().lower()
S1_LABEL = "s1-llm-auto-router by Decision Models (open weights)"
S1_URL = "https://huggingface.co/decisionmodels/s1-llm-auto-router"

S1 = s1_router.LocalS1Classifier(os.environ.get("DEMO_S1_MODEL") or s1_router.REPO,
                                 int(os.environ.get("DEMO_S1_THREADS") or 2))

#: Which route answers when Jev does not. Free and small on purpose.
FALLBACK_MODELS = ("dsv4-flash", "glm-5.3-flash", "nex-n2.5-pro", "glm-5.2-free")

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
    if classification.source_name == s1_router.LOCAL_SOURCE:
        return {"kind": "s1", "label": S1_LABEL, "model": classification.model or s1_router.MODEL_ID,
                "url": S1_URL}
    return {"kind": "jev", "label": "Jev by TypeSafe AI",
            "model": classification.model or jev.MODEL}


def warm() -> bool:
    """Load the model and run one request, so the first visitor does not wait for it."""
    return S1.warm()


def _native_difficulty(result: jev.Classification) -> jev.Classification:
    """A Jev answer with difficulty mapped onto the router's own scale."""
    offset, scale = JEV_CALIBRATION
    return dataclasses.replace(result, difficulty=max(0.0, min(1.0, (result.difficulty - offset) / scale)))


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
    """s1-llm-auto-router first, hosted Jev second, a small free model third, else ``None``."""
    if FIRST_CHOICE != "jev":
        result = await asyncio.to_thread(S1, request, context)
        if not result.failed:
            return result
        log.warning("s1-llm-auto-router did not answer; trying hosted Jev")
    result = await asyncio.to_thread(jev.classify, request, context)
    if not result.failed:
        return _native_difficulty(result)
    return await _fallback(request, context, client)
