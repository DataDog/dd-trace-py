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


@pytest.mark.snapshot
def test_system_one_apm_span_with_llmobs_disabled(typesafe_sdk, typesafe_client_default_model):
    """ddtrace-run users without LLMObs still get the APM span with shadow and gen_ai tags."""
    typesafe_client_default_model.system_one(state=STATE, questions=build_questions(typesafe_sdk))


@pytest.mark.snapshot(ignores=["meta.error.stack"])
def test_system_one_interrupted_finishes_span(typesafe_sdk):
    """An interrupted call still finishes its span, so the trace is not held in memory forever."""
    with typesafe_sdk.TypeSafeClient(
        api_key="not-a-real-key", transport=_InterruptingTransport(), retry=typesafe_sdk.RetryPolicy(max_retries=0)
    ) as client:
        with pytest.raises(_Interrupted):
            client.system_one(state=STATE, questions=build_questions(typesafe_sdk), model=MODEL)


@pytest.mark.snapshot(ignores=["meta.error.stack"])
@pytest.mark.asyncio
async def test_system_one_async_cancelled_finishes_span(typesafe_sdk):
    """A cancelled async call still finishes its span, so the trace is not held in memory forever."""
    async with typesafe_sdk.AsyncTypeSafeClient(
        api_key="not-a-real-key", transport=_CancellingTransport(), retry=typesafe_sdk.RetryPolicy(max_retries=0)
    ) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.system_one(state=STATE, questions=build_questions(typesafe_sdk), model=MODEL)
