"""The playground's answer check: who gets checked, and what the page is told.

The gate itself is the published router's and is tested there. What matters
here is the wiring: a cheap answer produces a chip, a frontier answer produces
a sentence saying why there is none, and a rejected answer produces a second
answer from a stronger route without the first one disappearing.
"""

import pytest

from app import verify
from auto_router.catalog import CacheRules, ModelInfo, Prices
from auto_router.jev import Judgement
from auto_router.verify import VerifyPolicy

CACHE = CacheRules(ttl_seconds=300, min_tokens=1024, hit_rate=0.9)


def model(name, inp, out, cap):
    return ModelInfo(name, "p", name, Prices(inp, out, inp / 10), CACHE,
                     capability={"coding": cap, "general": cap, "math": cap})


CHEAP = model("cheap", 0.05, 0.2, 45)
FRONTIER = model("frontier", 5.0, 25.0, 82)


@pytest.fixture
def judge(monkeypatch):
    """A local judge: no network, and it records what it was asked."""
    calls = []

    def fake(request, response, **kw):
        calls.append((request, response, kw))
        return Judgement(p_adequate=calls_p[0], latency_s=0.7, failure=calls_f[0],
                         model="jev-test")

    calls_p, calls_f = [0.04], ["incomplete"]
    monkeypatch.setattr(verify.jev, "judge", fake)
    return {"calls": calls, "p": calls_p, "failure": calls_f}


@pytest.mark.asyncio
async def test_a_cheap_answer_is_checked_and_the_chip_says_so(judge):
    judge["p"][0] = 0.93
    judge["failure"][0] = "fine"
    check = await verify.check(VerifyPolicy(), CHEAP, "coding", request="write median(xs)",
                               answer="def median(xs): ...", label="Cheap Route")
    assert check.checked and not check.escalate
    assert verify.chip_text(check) == "Checked by Jev: adequate (0.93)"
    assert judge["calls"][0][2]["category"] == "coding"


@pytest.mark.asyncio
async def test_a_rejected_answer_names_the_failure_and_the_route_it_escalated_to(judge):
    check = await verify.check(VerifyPolicy(), CHEAP, "coding", request="write median(xs)",
                               answer="def median(xs): pass", label="Cheap Route")
    assert check.checked and check.escalate
    assert verify.chip_text(check, "GPT-5.6 Sol") == (
        "Jev flagged it as incomplete (0.04) → escalated to GPT-5.6 Sol")
    assert "no stronger route" in verify.chip_text(check)


@pytest.mark.asyncio
async def test_a_frontier_answer_is_not_checked_and_the_page_is_told_why(judge):
    check = await verify.check(VerifyPolicy(), FRONTIER, "coding", request="write median(xs)",
                               answer="def median(xs): ...", label="Frontier")
    assert not check.checked and not check.escalate
    assert "above the verified cheap tier" in check.reason
    assert judge["calls"] == []
    assert verify.chip_text(check) == ""


@pytest.mark.asyncio
async def test_a_question_about_a_pasted_document_is_not_checked(judge):
    check = await verify.check(VerifyPolicy(), CHEAP, "long_context",
                               request="what does the contract say?", answer="nothing",
                               label="Cheap Route", needs_long_context=True)
    assert not check.checked
    assert "cannot see the document" in check.reason
    assert judge["calls"] == []


@pytest.mark.asyncio
async def test_a_judge_outage_is_reported_as_unchecked_not_as_a_failure(judge, monkeypatch):
    monkeypatch.setattr(verify.jev, "judge",
                        lambda *a, **k: Judgement(0.5, 0.0, failed=True))
    check = await verify.check(VerifyPolicy(), CHEAP, "coding", request="write median(xs)",
                               answer="def median(xs): pass", label="Cheap Route")
    assert not check.checked and not check.escalate and check.judge_failed


@pytest.mark.asyncio
async def test_the_verdict_payload_never_carries_the_answer(judge):
    check = await verify.check(VerifyPolicy(), CHEAP, "coding",
                               request="write median(xs)", answer="SECRET-ANSWER-TEXT",
                               label="Cheap Route")
    assert "SECRET-ANSWER-TEXT" not in repr(check.payload())


def test_the_escalation_example_is_still_on_the_page():
    """The one example that demonstrates the feature; losing it is silent."""
    import json
    from pathlib import Path
    rows = json.loads((Path(__file__).resolve().parents[1] / "app" / "data"
                       / "examples.json").read_text())
    assert any(r["id"] == "escalate" for r in rows)
