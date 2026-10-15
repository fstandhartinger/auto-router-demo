"""The classifier chain: open s1-llm-auto-router first, hosted Jev second, a free model third."""

import asyncio

import pytest

from auto_router import jev, s1_router

from app import classify


def _result(source="local-s1", difficulty=0.5, failed=False):
    return jev.Classification(
        category="coding", category_probs={"coding": 0.9}, category_confidence=0.9,
        difficulty=difficulty, difficulty_confidence=0.8, needs_tools=0.1, needs_vision=0.0,
        needs_long_context=0.0, follow_up=0.1, stakes=0.4, latency_s=0.02, model="m",
        source_name=source, failed=failed)


def run(coro):
    return asyncio.run(coro)


def test_s1_answers_first_and_hosted_jev_is_not_asked(monkeypatch):
    monkeypatch.setattr(classify, "FIRST_CHOICE", "s1")
    monkeypatch.setattr(classify, "S1", lambda request, context: _result())

    def forbidden(*a, **k):
        raise AssertionError("hosted Jev must not be called when s1-llm-auto-router answered")

    monkeypatch.setattr(jev, "classify", forbidden)
    result = run(classify.classify("write a function", "", None))
    assert result.source == "local-s1" and result.difficulty == 0.5
    meta = classify.classifier_meta(result)
    assert meta["kind"] == "s1" and meta["model"] == "m"
    assert meta["label"] == "s1-llm-auto-router by Decision Models (open weights)"
    assert meta["url"] == "https://huggingface.co/decisionmodels/s1-llm-auto-router"


def test_jev_fallback_is_mapped_onto_the_routers_difficulty_scale(monkeypatch):
    monkeypatch.setattr(classify, "FIRST_CHOICE", "s1")
    monkeypatch.setattr(classify, "S1", lambda request, context: jev.FALLBACK)
    # Jev level 2 of 0..4 reads 0.27 + 0.51 * 0.5 on its compressed scale.
    monkeypatch.setattr(jev, "classify", lambda request, context: _result("jev", 0.27 + 0.51 * 0.5))
    result = run(classify.classify("fix this bug", "", None))
    assert result.difficulty == pytest.approx(0.5)
    assert classify.classifier_meta(result)["kind"] == "jev"


def test_jev_difficulty_mapping_is_clamped(monkeypatch):
    monkeypatch.setattr(classify, "FIRST_CHOICE", "jev")
    monkeypatch.setattr(jev, "classify", lambda request, context: _result("jev", 0.05))
    assert run(classify.classify("hi", "", None)).difficulty == 0.0
    monkeypatch.setattr(jev, "classify", lambda request, context: _result("jev", 0.99))
    assert run(classify.classify("hard", "", None)).difficulty == 1.0


def test_first_choice_jev_never_loads_the_local_model(monkeypatch):
    monkeypatch.setattr(classify, "FIRST_CHOICE", "jev")

    def forbidden(request, context):
        raise AssertionError("the local model must not be used")

    monkeypatch.setattr(classify, "S1", forbidden)
    monkeypatch.setattr(jev, "classify", lambda request, context: _result("jev", 0.5))
    assert run(classify.classify("hi", "", None)) is not None


def test_nothing_answering_is_labelled_unavailable(monkeypatch):
    async def none(*a, **k):
        return None

    monkeypatch.setattr(classify, "FIRST_CHOICE", "s1")
    monkeypatch.setattr(classify, "S1", lambda request, context: jev.FALLBACK)
    monkeypatch.setattr(jev, "classify", lambda request, context: jev.FALLBACK)
    monkeypatch.setattr(classify, "_fallback", none)
    assert run(classify.classify("hi", "", None)) is None
    assert classify.classifier_meta(None)["kind"] == "unavailable"


def test_the_real_model_class_is_wired():
    assert isinstance(classify.S1, s1_router.LocalS1Classifier)
