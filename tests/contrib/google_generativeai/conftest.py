import os

import mock
import pytest

from ddtrace.contrib.internal.google_generativeai.patch import patch
from ddtrace.contrib.internal.google_generativeai.patch import unpatch
from ddtrace.llmobs import LLMObs
from ddtrace.trace import Pin
from tests.contrib.google_generativeai.utils import MockGenerativeModelAsyncClient
from tests.contrib.google_generativeai.utils import MockGenerativeModelClient
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config
from tests.utils import override_env
from tests.utils import override_global_config


def default_global_config():
    return {"_dd_api_key": "<not-a-real-api_key>"}


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def ddtrace_config_google_generativeai():
    return {}


@pytest.fixture
def mock_tracer(ddtrace_global_config, genai):
    try:
        pin = Pin.get_from(genai)
        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin._override(genai, tracer=mock_tracer)
        if ddtrace_global_config.get("_llmobs_enabled", False):
            # Have to disable and re-enable LLMObs to use to mock tracer.
            LLMObs.disable()
            LLMObs.enable(_tracer=mock_tracer, integrations_enabled=False)
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
def mock_client():
    yield MockGenerativeModelClient()


@pytest.fixture
def mock_client_async():
    yield MockGenerativeModelAsyncClient()


@pytest.fixture
def genai(ddtrace_global_config, ddtrace_config_google_generativeai, mock_client, mock_client_async):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        with override_config("google_generativeai", ddtrace_config_google_generativeai):
            with override_env(
                dict(GOOGLE_GENERATIVEAI_API_KEY=os.getenv("GOOGLE_GENERATIVEAI_API_KEY", "<not-a-real-key>"))
            ):
                patch()
                import google.generativeai as genai
                from google.generativeai import client as client_lib

                client_lib._client_manager.clients["generative"] = mock_client
                client_lib._client_manager.clients["generative_async"] = mock_client_async

                yield genai
                unpatch()
