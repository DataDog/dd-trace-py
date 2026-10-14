import json
from unittest import mock

import pytest

from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.llmobs._utils import assert_llmobs_span_data


COMMON_TAGS = {"ml_app": "<ml-app-name>", "service": "tests.contrib.typesafe", "integration": "typesafe"}
MODEL = "jev-1.13.0"
STATE = "I was charged twice for my subscription this month and want the duplicate refunded."
# Distinct from STATE so the error request gets its own cassette
ERROR_STATE = "Please cancel my account."
EXPECTED_QUESTIONS = {
    "team": {
        "type": "choice",
        "instructions": "Which team should handle this message?",
        "criteria": {
            "billing": "Charges, refunds, invoices",
            "technical": "Bugs, outages, errors",
            "sales": "Pricing, plans, buying more",
        },
    },
    "refund": {"type": "noul", "instructions": "Is the customer asking for a refund?"},
    "urgency": {"type": "score", "instructions": "How urgent is this message?", "criteria": ["Low", "Medium", "High"]},
}


def _questions(typesafe_sdk):
    team, refund, urgency = EXPECTED_QUESTIONS["team"], EXPECTED_QUESTIONS["refund"], EXPECTED_QUESTIONS["urgency"]
    return {
        "team": typesafe_sdk.Choice(instructions=team["instructions"], criteria=team["criteria"]),
        "refund": typesafe_sdk.Noul(instructions=refund["instructions"]),
        "urgency": typesafe_sdk.Score(instructions=urgency["instructions"], criteria=urgency["criteria"]),
    }


STRING_STATE_OUTPUT = (
    '{"refund": {"noul": 0.99, "type": "noul"}, "team": {"choice": "billing", "confidence": 1.0, "probabi'
    'lities": {"billing": 1.0, "sales": 0.0, "technical": 0.0}, "type": "choice"}, "urgency": {"confidenc'
    'e": 0.76, "legend": {"0": "Low", "1": "Medium", "2": "High"}, "probabilities": {"0": 0.02, "1": 0.84'
    ', "2": 0.14}, "score": 1.12, "type": "score"}}'
)
JSON_STATE_OUTPUT = (
    '{"refund": {"noul": 0.99, "type": "noul"}, "team": {"choice": "billing", "confidence": 1.0, "probabi'
    'lities": {"billing": 1.0, "sales": 0.0, "technical": 0.0}, "type": "choice"}, "urgency": {"confidenc'
    'e": 0.79, "legend": {"0": "Low", "1": "Medium", "2": "High"}, "probabilities": {"0": 0.02, "1": 0.86'
    ', "2": 0.12}, "score": 1.1, "type": "score"}}'
)
STRING_STATE_METRICS = {"input_tokens": 403, "output_tokens": 69, "total_tokens": 472}
JSON_STATE_METRICS = {"input_tokens": 417, "output_tokens": 69, "total_tokens": 486}


def _assert_system_one_span(span, input_content=STATE, output=STRING_STATE_OUTPUT, metrics=STRING_STATE_METRICS):
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name=MODEL,
        model_provider="typesafe",
        input_messages=[{"role": "user", "content": input_content}],
        output_messages=[{"role": "assistant", "content": output}],
        metadata={"questions": EXPECTED_QUESTIONS},
        metrics=metrics,
        tags=COMMON_TAGS,
    )


def test_system_one(typesafe_sdk, typesafe_client, typesafe_llmobs, test_spans):
    typesafe_client.system_one(state=STATE, questions=_questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    _assert_system_one_span(spans[0])


def test_system_one_positional_args(typesafe_sdk, typesafe_client, typesafe_llmobs, test_spans):
    typesafe_client.system_one(STATE, _questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    _assert_system_one_span(spans[0])


def test_system_one_json_state(typesafe_sdk, typesafe_client, typesafe_llmobs, test_spans):
    state = {"message": STATE, "plan": "pro"}
    typesafe_client.system_one(state=state, questions=_questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    _assert_system_one_span(
        spans[0], input_content=json.dumps(state, sort_keys=True), output=JSON_STATE_OUTPUT, metrics=JSON_STATE_METRICS
    )


@pytest.mark.asyncio
async def test_system_one_async(typesafe_sdk, async_typesafe_client, typesafe_llmobs, test_spans):
    async with async_typesafe_client as client:
        await client.system_one(state=STATE, questions=_questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    _assert_system_one_span(spans[0])


def test_system_one_error(typesafe_sdk, unauthenticated_typesafe_client, typesafe_llmobs, test_spans):
    with pytest.raises(typesafe_sdk.TypeSafeAuthenticationError):
        unauthenticated_typesafe_client.system_one(state=ERROR_STATE, questions=_questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name=MODEL,
        model_provider="typesafe",
        input_messages=[{"role": "user", "content": ERROR_STATE}],
        output_messages=[{"content": ""}],
        metadata={"questions": EXPECTED_QUESTIONS},
        error=mock.ANY,
        tags=COMMON_TAGS,
    )
    assert spans[0].error == 1
