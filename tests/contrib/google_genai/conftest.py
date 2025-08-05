import os
from typing import Any
from typing import Iterator
from unittest.mock import Mock
from unittest.mock import patch as mock_patch

import pytest

from ddtrace.contrib.internal.google_genai.patch import patch
from ddtrace.contrib.internal.google_genai.patch import unpatch
from ddtrace.llmobs import LLMObs
from ddtrace.trace import Pin
from tests.contrib.google_genai.utils import MOCK_EMBED_CONTENT_RESPONSE
from tests.contrib.google_genai.utils import MOCK_GENERATE_CONTENT_RESPONSE
from tests.contrib.google_genai.utils import MOCK_GENERATE_CONTENT_RESPONSE_STREAM
from tests.contrib.google_genai.utils import MOCK_TOOL_CALL_RESPONSE
from tests.contrib.google_genai.utils import MOCK_TOOL_FINAL_RESPONSE
from tests.llmobs._utils import TestLLMObsSpanWriter
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_global_config


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture(params=["google_genai", "vertex_ai"], ids=["google_genai", "vertex_ai"])
def genai_client(request, genai):
    """
    In order to test clients which face both gemini and vertexai, we provide this fixture which
    parametrizes the test case to run both cases.
    """
    if request.param == "vertex_ai":
        return genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "dummy-project"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
    return genai.Client()


@pytest.fixture
def genai_client_vcr(genai):
    return genai.Client(http_options={"base_url": "http://127.0.0.1:9126/vcr/genai"})


@pytest.fixture
def mock_tracer(ddtrace_global_config, genai):
    try:
        pin = Pin.get_from(genai)
        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin._override(genai, tracer=mock_tracer)
        yield mock_tracer
    except Exception:
        yield


@pytest.fixture
def genai_llmobs(mock_tracer, llmobs_span_writer):
    LLMObs.disable()
    with override_global_config(
        {
            "_dd_api_key": "<not-a-real-api_key>",
            "_llmobs_ml_app": "<ml-app-name>",
            "service": "tests.contrib.google_genai",
        }
    ):
        LLMObs.enable(_tracer=mock_tracer, integrations_enabled=False)
        LLMObs._instance._llmobs_span_writer = llmobs_span_writer
        yield LLMObs
    LLMObs.disable()


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(1.0, 5.0, is_agentless=True, _site="datad0g.com", _api_key="<not-a-real-key>")


@pytest.fixture
def llmobs_events(genai_llmobs, llmobs_span_writer):
    return llmobs_span_writer.events


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
def mock_generate_content(genai):
    def _fake_generate_content(self, *, model: str, contents, config=None):
        return MOCK_GENERATE_CONTENT_RESPONSE

    with mock_patch.object(genai.models.Models, "_generate_content", _fake_generate_content):
        yield


@pytest.fixture
def mock_async_generate_content(genai):
    async def _fake_async_generate_content(self, *, model: str, contents, config=None):
        return MOCK_GENERATE_CONTENT_RESPONSE

    with mock_patch.object(genai.models.AsyncModels, "_generate_content", _fake_async_generate_content):
        yield


@pytest.fixture
def mock_generate_content_stream(genai):
    def _fake_stream(self, *, model: str, contents, config=None) -> Iterator[Any]:
        for chunk in MOCK_GENERATE_CONTENT_RESPONSE_STREAM:
            yield chunk

    with mock_patch.object(genai.models.Models, "_generate_content_stream", _fake_stream):
        yield


@pytest.fixture
def mock_async_generate_content_stream(genai):
    async def _fake_async_stream(self, *, model: str, contents, config=None):
        async def _async_iterator():
            for chunk in MOCK_GENERATE_CONTENT_RESPONSE_STREAM:
                yield chunk

        return _async_iterator()

    with mock_patch.object(genai.models.AsyncModels, "_generate_content_stream", _fake_async_stream):
        yield


@pytest.fixture
def mock_generate_content_with_tools(genai):
    call_count = 0

    def _fake_generate_content_with_tools(self, *, model: str, contents, config=None):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            return MOCK_TOOL_CALL_RESPONSE
        return MOCK_TOOL_FINAL_RESPONSE

    with mock_patch.object(genai.models.Models, "_generate_content", _fake_generate_content_with_tools):
        yield


@pytest.fixture
def mock_embed_content(genai):
    """
    Since embed_content does not wrap a private helper like _generate_content,
    we patch the request method to avoid API calls and the _from_response method
    to return a correct response object.
    """

    def _fake_from_response(response, kwargs):
        return MOCK_EMBED_CONTENT_RESPONSE

    def _fake_request(self, method, path, request_dict, http_options):
        mock_response = Mock()
        mock_response.headers = {}
        mock_response.body = "{}"
        return mock_response

    with mock_patch.object(genai.types.EmbedContentResponse, "_from_response", _fake_from_response), mock_patch.object(
        genai._api_client.BaseApiClient, "request", _fake_request
    ):
        yield


@pytest.fixture
def mock_async_embed_content(genai):
    def _fake_from_response(response, kwargs):
        return MOCK_EMBED_CONTENT_RESPONSE

    async def _fake_async_request(self, method, path, request_dict, http_options):
        mock_response = Mock()
        mock_response.headers = {}
        mock_response.body = "{}"
        return mock_response

    with mock_patch.object(genai.types.EmbedContentResponse, "_from_response", _fake_from_response), mock_patch.object(
        genai._api_client.BaseApiClient, "async_request", _fake_async_request
    ):
        yield
