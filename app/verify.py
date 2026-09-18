"""The demo's side of verify-and-escalate: check a cheap answer, then show it.

All of the judgement lives in the published router
(``auto_router/verify.py``): which routes are cheap enough to check, what the
threshold is, which route a rejected answer escalates to. This module only does
the two things the library cannot: call the judge without blocking the event
loop, and turn a verdict into something a visitor can read.

Why only cheap answers are checked is the whole point, and the page says so
too: Jev is not stronger than a frontier model, so grading one would produce
false alarms rather than quality. Grading a *small* model's answer is a
different question, and the measured one.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from auto_router import jev, verify as router_verify

#: The demo runs on a capped budget, so a rejected answer buys exactly one
#: second attempt. The escalation target is above the checked tier anyway.
MAX_ESCALATIONS = 1


@dataclass(frozen=True)
class Check:
    """A verdict plus everything the page shows about it."""

    checked: bool
    p_adequate: float | None = None
    threshold: float | None = None
    escalate: bool = False
    failure: str = "unknown"
    latency_ms: float = 0.0
    reason: str = ""
    model: str = ""
    label: str = ""
    judge_failed: bool = False

    def payload(self) -> dict:
        return {
            "checked": self.checked,
            "p": None if self.p_adequate is None else round(self.p_adequate, 2),
            "threshold": self.threshold,
            "escalate": self.escalate,
            "failure": self.failure,
            "latencyMs": round(self.latency_ms),
            "reason": self.reason,
            "model": self.model,
            "label": self.label,
            "judgeFailed": self.judge_failed,
            "judge": "Jev by TypeSafe AI",
        }


#: What the chip says for each failure the judge can name.
FAILURE_WORDS = {
    "wrong": "wrong",
    "incomplete": "incomplete",
    "off_topic": "off topic",
    "fine": "fine",
    "unknown": "not adequate",
}


async def check(policy: router_verify.VerifyPolicy, model, category: str, *, request: str,
                answer: str, label: str, evidence_discount: float = 0.0,
                needs_long_context: bool = False) -> Check:
    """Ask Jev whether ``answer`` answers ``request`` - if this route qualifies.

    Every "no" is a sentence, not a silence: a visitor who sees no chip should
    be able to find out why, and "the judge cannot see the document this answer
    depends on" is one of the more interesting things this demo can teach.
    """
    applies, why = policy.applies(model, category, request_chars=len(request),
                                  needs_long_context=needs_long_context,
                                  evidence_discount=evidence_discount)
    if not applies:
        return Check(checked=False, reason=why, model=model.name, label=label)
    if not answer.strip():
        return Check(checked=False, reason="there is no answer to check",
                     model=model.name, label=label)
    started = time.perf_counter()
    judgement = await asyncio.to_thread(jev.judge, request, answer, category=category)
    latency_ms = (time.perf_counter() - started) * 1000
    verdict = router_verify.verdict_from(judgement, policy, category, model.name)
    if verdict.judge_failed:
        return Check(checked=False, reason="the judge did not answer in time",
                     model=model.name, label=label, judge_failed=True, latency_ms=latency_ms)
    return Check(checked=True, p_adequate=verdict.p_adequate, threshold=verdict.threshold,
                 escalate=verdict.escalate, failure=verdict.failure, latency_ms=latency_ms,
                 model=model.name, label=label)


def chip_text(check: Check, escalated_to: str | None = None) -> str:
    """The one line under an answer. Short, and never a euphemism."""
    if not check.checked:
        return ""
    if check.escalate:
        word = FAILURE_WORDS.get(check.failure, FAILURE_WORDS["unknown"])
        if escalated_to:
            return f"Jev flagged it as {word} ({check.p_adequate:.2f}) → escalated to {escalated_to}"
        return f"Jev flagged it as {word} ({check.p_adequate:.2f}) — no stronger route available"
    return f"Checked by Jev: adequate ({check.p_adequate:.2f})"
