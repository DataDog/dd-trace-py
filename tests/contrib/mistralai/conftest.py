import os

import mock
import pytest

from ddtrace.contrib.internal.mistralai.patch import patch
from ddtrace.contrib.internal.mistralai.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.utils import override_global_config


@pytest.fixture
def mistralai():
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
