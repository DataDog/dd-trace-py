import json
from unittest import mock

import pytest

from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.contrib.typesafe.utils import ERROR_STATE
from tests.contrib.typesafe.utils import EXPECTED_QUESTIONS
from tests.contrib.typesafe.utils import MODEL
from tests.contrib.typesafe.utils import STATE
from tests.contrib.typesafe.utils import build_questions
from tests.llmobs._utils import assert_llmobs_span_data


COMMON_TAGS = {"ml_app": "<ml-app-name>", "service": "tests.contrib.typesafe", "integration": "typesafe"}


# Full response bodies recorded in tests/cassettes/typesafe; only the urgency score differs between them
STRING_STATE_RESPONSE = {
    "answers": {
        "refund": {"noul": 0.99, "type": "noul"},
        "team": {
            "choice": "billing",
            "confidence": 1.0,
            "probabilities": {"billing": 1.0, "sales": 0.0, "technical": 0.0},
            "type": "choice",
        },
        "urgency": {
            "confidence": 0.76,
            "legend": {"0": "Low", "1": "Medium", "2": "High"},
            "probabilities": {"0": 0.02, "1": 0.84, "2": 0.14},
            "score": 1.12,
            "type": "score",
        },
    },
    "model": "jev-1.13.0",
    "usage": {"input_tokens": 403, "output_tokens": 69},
}
JSON_STATE_RESPONSE = {
    "answers": {
        "refund": {"noul": 0.99, "type": "noul"},
        "team": {
            "choice": "billing",
            "confidence": 1.0,
            "probabilities": {"billing": 1.0, "sales": 0.0, "technical": 0.0},
            "type": "choice",
        },
        "urgency": {
            "confidence": 0.79,
            "legend": {"0": "Low", "1": "Medium", "2": "High"},
            "probabilities": {"0": 0.02, "1": 0.86, "2": 0.12},
            "score": 1.1,
            "type": "score",
        },
    },
    "model": "jev-1.13.0",
    "usage": {"input_tokens": 417, "output_tokens": 69},
}
STRING_STATE_OUTPUT = json.dumps(STRING_STATE_RESPONSE, sort_keys=True)
JSON_STATE_OUTPUT = json.dumps(JSON_STATE_RESPONSE, sort_keys=True)
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
    typesafe_client.system_one(state=STATE, questions=build_questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    _assert_system_one_span(spans[0])


def test_system_one_positional_args(typesafe_sdk, typesafe_client, typesafe_llmobs, test_spans):
    typesafe_client.system_one(STATE, build_questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    _assert_system_one_span(spans[0])


def test_system_one_json_state(typesafe_sdk, typesafe_client, typesafe_llmobs, test_spans):
    state = {"message": STATE, "plan": "pro"}
    typesafe_client.system_one(state=state, questions=build_questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    _assert_system_one_span(
        spans[0], input_content=json.dumps(state, sort_keys=True), output=JSON_STATE_OUTPUT, metrics=JSON_STATE_METRICS
    )


@pytest.mark.asyncio
async def test_system_one_async(typesafe_sdk, async_typesafe_client, typesafe_llmobs, test_spans):
    async with async_typesafe_client as client:
        await client.system_one(state=STATE, questions=build_questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    _assert_system_one_span(spans[0])


def test_system_one_error(typesafe_sdk, unauthenticated_typesafe_client, typesafe_llmobs, test_spans):
    with pytest.raises(typesafe_sdk.TypeSafeAuthenticationError):
        unauthenticated_typesafe_client.system_one(
            state=ERROR_STATE, questions=build_questions(typesafe_sdk), model=MODEL
        )
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


@pytest.mark.subprocess(env={"DD_API_KEY": "<not-a-real-key>"})
def test_llmobs_enable_patches_typesafe():
    """LLMObs.enable() with default integrations patches typesafe-sdk, and a bad registration name breaks it."""
    from ddtrace.llmobs import LLMObs

    LLMObs.enable(ml_app="<ml-app-name>", agentless_enabled=False)
    import typesafe_sdk

    assert LLMObs.enabled
    assert getattr(typesafe_sdk, "_datadog_patch", False)
    LLMObs.disable()
