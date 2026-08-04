import os
from unittest import mock

import pytest

from ddtrace.contrib.internal.litellm.patch import patch
from ddtrace.contrib.internal.litellm.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.contrib.litellm.utils import get_request_vcr
from tests.contrib.litellm.utils import model_list
from tests.utils import override_global_config


@pytest.fixture
def litellm(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "<not-a-real-key>")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "<not-a-real-key>")
    monkeypatch.setenv("COHERE_API_KEY", "<not-a-real-key>")
    monkeypatch.setenv("OPENROUTER_API_KEY", "<not-a-real-key>")
    patch()
    import litellm

    # Pin tiktoken to litellm's bundled encoding cache so token counting never hits the network.
    # Unrecognized model slugs fall back to tiktoken.get_encoding("cl100k_base"), which would
    # otherwise fetch the encoding over HTTP (unrecordable / fails in CI).
    monkeypatch.setenv(
        "TIKTOKEN_CACHE_DIR", os.path.join(os.path.dirname(litellm.__file__), "litellm_core_utils", "tokenizers")
    )
    yield litellm
    unpatch()


@pytest.fixture
def litellm_llmobs(tracer, monkeypatch):
    LLMObs.disable()
    with override_global_config(
        {
            "_llmobs_ml_app": "<ml-app-name>",
            "_dd_api_key": "<not-a-real-key>",
        }
    ):
        LLMObs.enable(_tracer=tracer, integrations_enabled=False)
        LLMObs._instance._llmobs_span_writer.stop()
        LLMObs._instance._llmobs_span_writer = mock.MagicMock()
        yield LLMObs
    LLMObs.disable()


@pytest.fixture
def request_vcr():
    return get_request_vcr()


@pytest.fixture
def request_vcr_include_localhost():
    return get_request_vcr(ignore_localhost=False)


@pytest.fixture
def router():
    from litellm import Router

    yield Router(model_list=model_list)
