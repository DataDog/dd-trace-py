import os
from typing import Any
from typing import Iterator
from unittest.mock import patch as mock_patch

import mock
import pytest

from ddtrace.contrib.internal.google_genai.patch import patch
from ddtrace.contrib.internal.google_genai.patch import unpatch
from ddtrace.llmobs import LLMObs
from ddtrace.trace import Pin
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_global_config


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def mock_tracer(ddtrace_global_config, genai):
    try:
        pin = Pin.get_from(genai)
        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin._override(genai, tracer=mock_tracer)
        pin.tracer.configure()
        if ddtrace_global_config.get("_llmobs_enabled", False):
            LLMObs.disable()
            LLMObs.enable(_tracer=mock_tracer, integrations_enabled=False, agentless_enabled=False)
        yield mock_tracer
    except Exception:
        yield


@pytest.fixture
def mock_llmobs_writer():
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
    try:
        LLMObsSpanWriterMock = patcher.start()
        m = mock.MagicMock()
        LLMObsSpanWriterMock.return_value = m
        yield m
    finally:
        patcher.stop()


@pytest.fixture
def genai(ddtrace_global_config):
    # tests require that these environment variables are set (especially for remote CI testing)
    os.environ["GOOGLE_CLOUD_LOCATION"] = "<not-a-real-location>"
    os.environ["GOOGLE_CLOUD_PROJECT"] = "<not-a-real-project>"
    os.environ["GOOGLE_API_KEY"] = "<not-a-real-key>"

    with override_global_config(ddtrace_global_config):
        patch()
        from google import genai

        yield genai
        unpatch()


@pytest.fixture
def mock_generate_content():
    from google import genai

    from .utils import MOCK_GENERATE_CONTENT_RESPONSE

    def _fake_generate_content(self, *, model: str, contents, config=None):
        return MOCK_GENERATE_CONTENT_RESPONSE

    with mock_patch.object(genai.models.Models, "_generate_content", _fake_generate_content):
        yield


@pytest.fixture
def mock_async_generate_content():
    from google import genai

    from .utils import MOCK_GENERATE_CONTENT_RESPONSE

    async def _fake_async_generate_content(self, *, model: str, contents, config=None):
        return MOCK_GENERATE_CONTENT_RESPONSE

    with mock_patch.object(genai.models.AsyncModels, "_generate_content", _fake_async_generate_content):
        yield


@pytest.fixture
def mock_generate_content_stream():
    from google import genai

    from .utils import MOCK_GENERATE_CONTENT_RESPONSE_STREAM

    def _fake_stream(self, *, model: str, contents, config=None) -> Iterator[Any]:
        for chunk in MOCK_GENERATE_CONTENT_RESPONSE_STREAM:
            yield chunk

    with mock_patch.object(genai.models.Models, "_generate_content_stream", _fake_stream):
        yield


@pytest.fixture
def mock_async_generate_content_stream():
    from google import genai

    from .utils import MOCK_GENERATE_CONTENT_RESPONSE_STREAM

    async def _fake_async_stream(self, *, model: str, contents, config=None):
        async def _async_iterator():
            for chunk in MOCK_GENERATE_CONTENT_RESPONSE_STREAM:
                yield chunk

        return _async_iterator()

    with mock_patch.object(genai.models.AsyncModels, "_generate_content_stream", _fake_async_stream):
        yield


@pytest.fixture
def mock_generate_content_with_tools():
    from google import genai

    from .utils import MOCK_TOOL_CALL_RESPONSE
    from .utils import MOCK_TOOL_FINAL_RESPONSE

    call_count = 0

    def _fake_generate_content_with_tools(self, *, model: str, contents, config=None):
        nonlocal call_count
        call_count += 1

        # First call returns tool call, second call returns final response
        if call_count == 1:
            return MOCK_TOOL_CALL_RESPONSE
        else:
            return MOCK_TOOL_FINAL_RESPONSE

    with mock_patch.object(genai.models.Models, "_generate_content", _fake_generate_content_with_tools):
        yield
