from unittest import mock

import pytest

from ddtrace.contrib.internal.elevenlabs.patch import patch
from ddtrace.contrib.internal.elevenlabs.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def elevenlabs_llmobs(tracer):
    LLMObs.disable()
    with override_global_config({"_llmobs_ml_app": "elevenlabs-tests", "_dd_api_key": "test-key"}):
        # Preserve the tracer fixture's DummyWriter; agentless mode replaces it.
        LLMObs.enable(_tracer=tracer, integrations_enabled=False, agentless_enabled=False)
        LLMObs._instance._llmobs_span_writer.stop()
        LLMObs._instance._llmobs_span_writer = mock.MagicMock()
        patch()
        yield LLMObs
        unpatch()
    LLMObs.disable()
