import asyncio

import httpx2
import pytest

from tests.contrib.typesafe.utils import MODEL
from tests.contrib.typesafe.utils import STATE
from tests.contrib.typesafe.utils import build_questions


class _Interrupted(BaseException):
    """Stands in for BaseExceptions such as gevent.Timeout that skip `except Exception` handlers."""


class _InterruptingTransport(httpx2.BaseTransport):
    def handle_request(self, request):
        raise _Interrupted()


class _CancellingTransport(httpx2.AsyncBaseTransport):
    async def handle_async_request(self, request):
        raise asyncio.CancelledError()


def test_system_one_apm_span_with_llmobs_disabled(typesafe_sdk, typesafe_client_default_model, test_spans):
    """ddtrace-run users without LLMObs still get the APM span with shadow and gen_ai tags."""
    typesafe_client_default_model.system_one(state=STATE, questions=build_questions(typesafe_sdk))
    spans = test_spans.pop()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "typesafe.request"
    assert span.resource == "TypeSafeClient.system_one"
    assert span.error == 0
    assert span.get_tag("typesafe.request.model") == MODEL
    assert span.get_tag("_dd.llmobs.span_kind") == "llm"
    assert span.get_tag("_dd.llmobs.model_name") == MODEL
    assert span.get_tag("_dd.llmobs.model_provider") == "typesafe"
    assert span.get_metric("_dd.llmobs.enabled") == 0
    assert span.get_metric("_dd.llmobs.input_tokens") == 403
    assert span.get_metric("_dd.llmobs.output_tokens") == 69
    assert span.get_tag("gen_ai.request.model") == MODEL
    assert span.get_tag("gen_ai.provider.name") == "typesafe"
    assert span.get_metric("gen_ai.usage.input_tokens") == 403
    assert span.get_metric("gen_ai.usage.output_tokens") == 69


def test_system_one_interrupted_finishes_span(typesafe_sdk, test_spans):
    """An interrupted call still finishes its span, so the trace is not held in memory forever."""
    with typesafe_sdk.TypeSafeClient(
        api_key="not-a-real-key", transport=_InterruptingTransport(), retry=typesafe_sdk.RetryPolicy(max_retries=0)
    ) as client:
        with pytest.raises(_Interrupted):
            client.system_one(state=STATE, questions=build_questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    assert spans[0].error == 1


@pytest.mark.asyncio
async def test_system_one_async_cancelled_finishes_span(typesafe_sdk, test_spans):
    """A cancelled async call still finishes its span, so the trace is not held in memory forever."""
    async with typesafe_sdk.AsyncTypeSafeClient(
        api_key="not-a-real-key", transport=_CancellingTransport(), retry=typesafe_sdk.RetryPolicy(max_retries=0)
    ) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.system_one(state=STATE, questions=build_questions(typesafe_sdk), model=MODEL)
    spans = test_spans.pop()
    assert len(spans) == 1
    assert spans[0].error == 1
