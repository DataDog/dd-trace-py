"""Shared span-inspection helpers for AI Guard + OpenAI integration tests.

Both the Chat Completions test suite (``test_openai.py``) and the Responses
API test suite (``test_responses.py``) need to verify the same dual-span
contract on AI Guard block: an AI Guard span tagged with the block decision
AND an openai/LLMObs span finished with ``error=1`` and an ``AIGuardAbortError``
``error.type`` tag. The helpers live in a shared module so a regression in
either OpenAI surface fails the same assertions.
"""

from ddtrace.appsec._constants import AI_GUARD


def find_ai_guard_span(spans):
    """Return the AI Guard span from ``spans`` or ``None``.

    Identified by ``span.name == AI_GUARD.RESOURCE_TYPE`` — the AI Guard
    instrumentation reuses the same span name across providers, so this
    helper is provider-agnostic.
    """
    for span in spans:
        if span.name == AI_GUARD.RESOURCE_TYPE:
            return span
    return None


def find_openai_span(spans):
    """Return the openai/LLMObs span from ``spans`` or ``None``.

    Prefers a span whose ``service`` contains ``"openai"`` (set by the
    openai integration); falls back to any non-AI-Guard span so tests still
    locate the LLM span when ``service`` is overridden by a parent setting.
    """
    for span in spans:
        if span.name != AI_GUARD.RESOURCE_TYPE and "openai" in (span.service or "").lower():
            return span
    for span in spans:
        if span.name != AI_GUARD.RESOURCE_TYPE:
            return span
    return None


def assert_block_emits_both_spans(test_spans, decision):
    """Assert the dual-span contract on an AI Guard block decision.

    Both the AI Guard span (with action/blocked tags) AND the openai/LLMObs
    span (with ``error=1`` and an ``AIGuardAbortError`` ``error.type``) must
    be emitted, otherwise a downstream Datadog UI would lose visibility into
    either the block decision or the blocked LLM call.
    """
    spans = test_spans.spans
    ai_guard_span = find_ai_guard_span(spans)
    assert ai_guard_span is not None, f"AI Guard span not found in {[s.name for s in spans]}"
    assert ai_guard_span.get_tag(AI_GUARD.ACTION_TAG) == decision
    assert ai_guard_span.get_tag(AI_GUARD.BLOCKED_TAG) == "true"

    openai_span = find_openai_span(spans)
    assert openai_span is not None, f"OpenAI/LLMObs span not found in {[s.name for s in spans]}"
    assert openai_span.error == 1, "OpenAI span should have error=1 after AI Guard block"
    assert "AIGuardAbortError" in (openai_span.get_tag("error.type") or ""), (
        f"OpenAI span error.type should reference AIGuardAbortError, got: {openai_span.get_tag('error.type')!r}"
    )
