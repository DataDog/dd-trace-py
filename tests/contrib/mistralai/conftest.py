import os

import httpx
import mock
import pytest

from ddtrace.contrib.internal.mistralai.patch import patch
from ddtrace.contrib.internal.mistralai.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.contrib.mistralai.utils import CHAT_COMPLETION_BODY
from tests.contrib.mistralai.utils import CHAT_COMPLETION_WITH_CACHED_TOKENS_BODY
from tests.contrib.mistralai.utils import EMBEDDING_BODY
from tests.contrib.mistralai.utils import TOOL_CALL_BODY
from tests.contrib.mistralai.utils import TOOL_FINAL_RESPONSE_BODY
from tests.utils import override_global_config


def _make_do_request(body):
    def _fake(self, *_args, **_kwargs):
        return httpx.Response(200, json=body)

    return _fake


def _make_do_request_async(body):
    async def _fake(self, *_args, **_kwargs):
        return httpx.Response(200, json=body)

    return _fake


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


@pytest.fixture
def mock_chat_complete(mistralai):
    from mistralai.client.chat import Chat

    with mock.patch.object(Chat, "do_request", _make_do_request(CHAT_COMPLETION_BODY)):
        yield


@pytest.fixture
def mock_async_chat_complete(mistralai):
    from mistralai.client.chat import Chat

    with mock.patch.object(Chat, "do_request_async", _make_do_request_async(CHAT_COMPLETION_BODY)):
        yield


@pytest.fixture
def mock_chat_complete_with_tools(mistralai):
    from mistralai.client.chat import Chat

    call_count = 0

    def _fake_chat_complete_with_tools(self, *_args, **_kwargs):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            return httpx.Response(200, json=TOOL_CALL_BODY)
        return httpx.Response(200, json=TOOL_FINAL_RESPONSE_BODY)

    with mock.patch.object(Chat, "do_request", _fake_chat_complete_with_tools):
        yield


@pytest.fixture
def mock_async_chat_complete_with_tools(mistralai):
    from mistralai.client.chat import Chat

    call_count = 0

    async def _fake_async_chat_complete_with_tools(self, *_args, **_kwargs):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            return httpx.Response(200, json=TOOL_CALL_BODY)
        return httpx.Response(200, json=TOOL_FINAL_RESPONSE_BODY)

    with mock.patch.object(Chat, "do_request_async", _fake_async_chat_complete_with_tools):
        yield


@pytest.fixture
def mock_chat_complete_with_cached_tokens(mistralai):
    from mistralai.client.chat import Chat

    with mock.patch.object(Chat, "do_request", _make_do_request(CHAT_COMPLETION_WITH_CACHED_TOKENS_BODY)):
        yield


@pytest.fixture
def mock_async_chat_complete_with_cached_tokens(mistralai):
    from mistralai.client.chat import Chat

    with mock.patch.object(Chat, "do_request_async", _make_do_request_async(CHAT_COMPLETION_WITH_CACHED_TOKENS_BODY)):
        yield


@pytest.fixture
def mock_embed_create(mistralai):
    from mistralai.client.embeddings import Embeddings

    with mock.patch.object(Embeddings, "do_request", _make_do_request(EMBEDDING_BODY)):
        yield


@pytest.fixture
def mock_async_embed_create(mistralai):
    from mistralai.client.embeddings import Embeddings

    with mock.patch.object(Embeddings, "do_request_async", _make_do_request_async(EMBEDDING_BODY)):
        yield
