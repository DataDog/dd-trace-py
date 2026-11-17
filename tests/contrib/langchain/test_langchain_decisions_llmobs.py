import json
import os
import sys
import types
from unittest import mock

import pytest
import wrapt

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.llmobs._utils import assert_llmobs_span_data


decisions = pytest.importorskip("langchain_core.decisions")

TICKET = "I was charged twice for my subscription this month and want the duplicate refunded."
TYPESAFE_URL = "http://localhost:9126/vcr/typesafe"
TAGS = {"ml_app": "langchain_test", "integration": "langchain"}


def _request():
    return {
        "state": TICKET,
        "questions": {
            "team": decisions.Choice(
                instructions="Which team should handle this message?",
                criteria={"billing": "Payment or refund issues.", "support": "Product bugs or outages."},
            )
        },
    }


def _expected_input(model):
    return json.dumps(
        {
            "model": model,
            "questions": {
                "team": {
                    "criteria": {"billing": "Payment or refund issues.", "support": "Product bugs or outages."},
                    "instructions": "Which team should handle this message?",
                    "type": "choice",
                }
            },
            "state": TICKET,
        },
        sort_keys=True,
    )


def _llmobs_spans(test_spans):
    return [
        _get_llmobs_data_metastruct(s)
        for trace in test_spans.pop_traces()
        for s in trace
        if _get_llmobs_data_metastruct(s)
    ]


def _assert_llm_decision_span(span_data, response, request_model, provider):
    output = response.model_dump(mode="json", exclude_none=True)
    output.pop("usage")
    assert_llmobs_span_data(
        span_data,
        span_kind="llm",
        model_name=response.model,
        model_provider=provider,
        input_messages=[{"role": "user", "content": mock.ANY}],
        output_messages=[{"role": "assistant", "content": mock.ANY}],
        metrics={
            "input_tokens": response.usage["input_tokens"],
            "output_tokens": response.usage["output_tokens"],
            "total_tokens": response.usage["total_tokens"],
        },
        tags={**TAGS, "decision_provider": provider},
    )
    meta = span_data[LLMOBS_STRUCT.META]
    assert json.dumps(
        json.loads(meta[LLMOBS_STRUCT.INPUT][LLMOBS_STRUCT.MESSAGES][0]["content"]), sort_keys=True
    ) == _expected_input(request_model)
    assert json.loads(meta[LLMOBS_STRUCT.OUTPUT][LLMOBS_STRUCT.MESSAGES][0]["content"]) == output


def test_llmobs_openai_classifier(langchain_core, langchain_llmobs, test_spans, openai_url):
    classifier_mod = pytest.importorskip("langchain_openai.decisions")
    classifier = classifier_mod.OpenAIClassifier(model="gpt-6-luna", base_url=openai_url, max_retries=0)
    response = classifier.invoke(_request())

    spans = _llmobs_spans(test_spans)
    assert len(spans) == 1
    _assert_llm_decision_span(spans[0], response, "gpt-6-luna", "openai")


@pytest.mark.asyncio
async def test_llmobs_openai_classifier_async(langchain_core, langchain_llmobs, test_spans, openai_url):
    classifier_mod = pytest.importorskip("langchain_openai.decisions")
    classifier = classifier_mod.OpenAIClassifier(model="gpt-6-luna", base_url=openai_url, max_retries=0)
    response = await classifier.ainvoke(_request())

    spans = _llmobs_spans(test_spans)
    assert len(spans) == 1
    _assert_llm_decision_span(spans[0], response, "gpt-6-luna", "openai")


def _typesafe_classifier():
    langchain_typesafe = pytest.importorskip("langchain_typesafe")
    return langchain_typesafe.TypeSafeClassifier(
        model="jev-latest",
        base_url=TYPESAFE_URL,
        api_key=os.getenv("TYPESAFE_API_KEY", "<not-a-real-key>"),
    )


def test_llmobs_typesafe_classifier(langchain_core, langchain_llmobs, test_spans):
    response = _typesafe_classifier().invoke(_request())

    spans = _llmobs_spans(test_spans)
    assert len(spans) == 1
    _assert_llm_decision_span(spans[0], response, "jev-latest", "typesafe")


@pytest.mark.asyncio
async def test_llmobs_typesafe_classifier_async(langchain_core, langchain_llmobs, test_spans):
    response = await _typesafe_classifier().ainvoke(_request())

    spans = _llmobs_spans(test_spans)
    assert len(spans) == 1
    _assert_llm_decision_span(spans[0], response, "jev-latest", "typesafe")


class _GoogleDecisionModel(decisions.BaseDecisionModel):
    model: str = "gemini-decision-1"

    @property
    def _provider(self):
        return "google_genai"

    def _classify(self, state, questions):
        return decisions.ClassifierResponse(
            model=self.model,
            answers={
                "team": {"type": "choice", "choice": "billing", "probabilities": {"billing": 1.0}, "confidence": 1.0}
            },
            usage={"input_tokens": 10, "output_tokens": 2},
        )


@pytest.fixture
def traced_google_decisions(monkeypatch):
    class Decisions:
        create = wrapt.FunctionWrapper(lambda *a, **k: None, lambda f, i, a, k: f(*a, **k))

    module = types.ModuleType("google.genai.decisions")
    module.Decisions = Decisions
    monkeypatch.setitem(sys.modules, "google.genai.decisions", module)
    monkeypatch.setattr(LLMObs, "_integration_is_enabled", staticmethod(lambda name: name == "google_genai"))


def test_llmobs_decision_without_provider_span_is_llm(langchain_core, langchain_llmobs, test_spans):
    response = _GoogleDecisionModel().invoke(_request())

    spans = _llmobs_spans(test_spans)
    assert len(spans) == 1
    _assert_llm_decision_span(spans[0], response, "gemini-decision-1", "google")


# Simulates a traced google.genai Decisions.create; it proves the kind and token rule, not a real child span.
def test_llmobs_decision_with_provider_span_is_workflow(
    langchain_core, langchain_llmobs, test_spans, traced_google_decisions
):
    _GoogleDecisionModel().invoke(_request())

    spans = _llmobs_spans(test_spans)
    assert len(spans) == 1
    assert_llmobs_span_data(
        spans[0],
        span_kind="workflow",
        input_value=mock.ANY,
        output_value=mock.ANY,
        tags={**TAGS, "decision_provider": "google"},
    )
    assert json.dumps(
        json.loads(spans[0][LLMOBS_STRUCT.META][LLMOBS_STRUCT.INPUT][LLMOBS_STRUCT.VALUE]), sort_keys=True
    ) == _expected_input("gemini-decision-1")
    assert not {"input_tokens", "output_tokens", "total_tokens"} & set(spans[0].get(LLMOBS_STRUCT.METRICS, {}))
