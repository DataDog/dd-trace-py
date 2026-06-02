import os

import mock
import pytest
import vcr

from ddtrace.contrib.internal.openai.patch import patch as patch_openai
from ddtrace.contrib.internal.openai.patch import unpatch as unpatch_openai
from ddtrace.contrib.internal.pydantic_ai.patch import patch
from ddtrace.contrib.internal.pydantic_ai.patch import unpatch
from ddtrace.llmobs import LLMObs as llmobs_service
from tests.utils import override_global_config


def default_global_config():
    return {}


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture(autouse=True)
def pydantic_ai(ddtrace_global_config, monkeypatch):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        monkeypatch.setenv("OPENAI_API_KEY", "<not-a-real-key>")
        patch()
        import pydantic_ai

        yield pydantic_ai
        unpatch()


@pytest.fixture
def openai_patched():
    patch_openai()
    import openai

    yield openai
    unpatch_openai()


@pytest.fixture
def pydantic_ai_llmobs(tracer, monkeypatch):
    # Preserve meta_struct["_llmobs"] on spans so tests can assert against
    # LLMObsSpanData via _get_llmobs_data_metastruct; production scrubs it after
    # enqueueing to LLMObsSpanWriter.
    monkeypatch.setenv("_DD_LLMOBS_TEST_KEEP_META_STRUCT", "1")
    llmobs_service.disable()
    with override_global_config(
        {
            "_llmobs_ml_app": "<ml-app-name>",
            "_dd_api_key": "<not-a-real-key>",
        }
    ):
        llmobs_service.enable(_tracer=tracer, integrations_enabled=False)
        # Replace the real LLMObsSpanWriter with a mock so we don't keep a
        # background flush thread alive trying to ship spans during the test.
        llmobs_service._instance._llmobs_span_writer.stop()
        llmobs_service._instance._llmobs_span_writer = mock.MagicMock()
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def request_vcr(ignore_localhost=True):
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "x-api-key", "api-key"],
        ignore_localhost=ignore_localhost,
    )
