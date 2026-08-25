import os

import mock
import pytest

from ddtrace.contrib.internal.mistralai.patch import patch
from ddtrace.contrib.internal.mistralai.patch import unpatch
from ddtrace.llmobs import LLMObs
from ddtrace.trace import tracer
from tests.utils import override_global_config


@pytest.fixture
def mistralai():
    # Ensure a clean slate before each test. Snapshot tests do not reset the
    # global tracer between runs, so a previous test (typically an async
    # streaming case whose async generator was not finalized until later) can
    # leave a ``mistralai.request`` span active on the tracer. That leaked
    # span would then become the parent of this test's span and corrupt the
    # span/trace counts asserted here and in snapshot comparisons. Drop any
    # active span and flush pending traces before patching.
    tracer.context_provider.activate(None)
    try:
        tracer._span_aggregator.writer.flush_queue()
    except Exception:
        pass

    patch()
    from mistralai.client.sdk import Mistral

    yield Mistral
    unpatch()


@pytest.fixture
def mistral_client(mistralai):
    return mistralai(
        api_key=os.getenv("MISTRAL_API_KEY", "<not-a-real-key>"), server_url="http://127.0.0.1:9126/vcr/mistral"
    )


@pytest.fixture
def mistralai_llmobs():
    LLMObs.disable()
    with override_global_config(
        {
            "_llmobs_ml_app": "<ml-app-name>",
            "_dd_api_key": "<not-a-real-key>",
        }
    ):
        LLMObs.enable(integrations_enabled=False)
        LLMObs._instance._llmobs_span_writer.stop()
        LLMObs._instance._llmobs_span_writer = mock.MagicMock()
        yield LLMObs
    LLMObs.disable()
