"""Talking to the OpenAI-compatible endpoints behind the demo's routes.

The browser never sees a provider name, a base URL or a key: it asks the demo
for an answer from the *routed model*, and the server decides which endpoint
that means. Everything is capped - prompt length, output length, wall clock -
because this is a public playground, not a free inference service.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import AsyncIterator

import httpx

from .settings import SETTINGS

REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=20.0, pool=10.0)


@dataclass(frozen=True)
class Route:
    """Where one catalog model is actually served."""

    model: str
    provider: str
    base_url: str
    api_key: str | None
    upstream_id: str
    extra_headers: dict
    request_extra: dict

    @property
    def usable(self) -> bool:
        return bool(self.base_url and self.upstream_id)


def _providers() -> dict:
    return (SETTINGS.routes or {}).get("providers") or {}


def _route_entries() -> dict:
    return (SETTINGS.routes or {}).get("routes") or {}


def route_for(model: str) -> Route | None:
    import os

    entry = _route_entries().get(model)
    if not entry:
        return None
    provider = _providers().get(entry.get("provider") or "")
    if not provider:
        return None
    key_env = provider.get("api_key_env")
    return Route(
        model=model,
        provider=entry.get("provider") or "",
        base_url=str(provider.get("base_url") or "").rstrip("/"),
        api_key=os.environ.get(key_env) if key_env else None,
        upstream_id=str(entry.get("upstream_id") or ""),
        extra_headers=provider.get("extra_headers") or {},
        request_extra=entry.get("request_extra") or {},
    )


def executable_models() -> set[str]:
    return {name for name in _route_entries() if (route_for(name) or Route("", "", "", None, "", {}, {})).usable}


def _headers(route: Route) -> dict:
    head = {"Content-Type": "application/json", **route.extra_headers}
    if route.api_key:
        head["Authorization"] = f"Bearer {route.api_key}"
    return head


def _body(route: Route, messages: list[dict], max_tokens: int, stream: bool) -> dict:
    return {
        "model": route.upstream_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": stream,
        **({"stream_options": {"include_usage": True}} if stream else {}),
        **route.request_extra,
    }


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0


def _usage_from(payload: dict) -> Usage:
    usage = payload.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    return Usage(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        cached_tokens=int(details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0),
    )


async def stream_answer(route: Route, messages: list[dict], max_tokens: int,
                        client: httpx.AsyncClient) -> AsyncIterator[tuple[str, object]]:
    """Yield ``("delta", text)``, ``("reasoning", text)``, ``("usage", Usage)`` or ``("error", str)``."""
    body = _body(route, messages, max_tokens, stream=True)
    usage = Usage()
    try:
        async with client.stream("POST", f"{route.base_url}/chat/completions",
                                 json=body, headers=_headers(route),
                                 timeout=REQUEST_TIMEOUT) as resp:
            if resp.status_code >= 400:
                detail = (await resp.aread()).decode("utf-8", "replace")[:300]
                yield "error", f"upstream {resp.status_code}: {detail}"
                return
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if payload.get("usage"):
                    usage = _usage_from(payload)
                for choice in payload.get("choices") or []:
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                    if reasoning:
                        yield "reasoning", reasoning
                    content = delta.get("content")
                    if content:
                        yield "delta", content
    except (httpx.HTTPError, ValueError) as exc:
        yield "error", f"{type(exc).__name__}: {exc}"
        return
    yield "usage", usage


async def complete(route: Route, messages: list[dict], max_tokens: int,
                   client: httpx.AsyncClient) -> tuple[str, Usage, str | None]:
    """One non-streaming completion. Returns ``(text, usage, error)``."""
    try:
        resp = await client.post(f"{route.base_url}/chat/completions",
                                 json=_body(route, messages, max_tokens, stream=False),
                                 headers=_headers(route), timeout=REQUEST_TIMEOUT)
    except httpx.HTTPError as exc:
        return "", Usage(), f"{type(exc).__name__}: {exc}"
    if resp.status_code >= 400:
        return "", Usage(), f"upstream {resp.status_code}"
    payload = resp.json()
    choices = payload.get("choices") or [{}]
    text = ((choices[0].get("message") or {}).get("content")) or ""
    return text, _usage_from(payload), None
