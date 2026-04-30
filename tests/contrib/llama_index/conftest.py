import os
from unittest import mock

import pytest

from ddtrace.contrib.internal.llama_index.patch import patch
from ddtrace.contrib.internal.llama_index.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.contrib.llama_index.utils import get_request_vcr
from tests.utils import override_config
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.fixture
def ddtrace_config_llama_index():
    return {}


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def test_spans(ddtrace_global_config, test_spans, monkeypatch):
    try:
        if ddtrace_global_config.get("_llmobs_enabled", False):
            # Preserve meta_struct["_llmobs"] on spans so tests can assert against
            # LLMObsSpanData via _get_llmobs_data_metastruct; production scrubs it
            # after enqueueing to LLMObsSpanWriter.
            monkeypatch.setenv("_DD_LLMOBS_TEST_KEEP_META_STRUCT", "1")
            with override_global_config(ddtrace_global_config):
                # Have to disable and re-enable LLMObs to use to mock tracer.
                LLMObs.disable()
                LLMObs.enable(_tracer=test_spans.tracer, integrations_enabled=False)
                # Replace the real LLMObsSpanWriter with a mock so we don't keep a
                # background flush thread alive trying to ship spans during the test.
                LLMObs._instance._llmobs_span_writer.stop()
                LLMObs._instance._llmobs_span_writer = mock.MagicMock()
                yield test_spans
        else:
            yield test_spans
    finally:
        LLMObs.disable()


def default_global_config():
    return {"_dd_api_key": "<not-a-real-api_key>"}


@pytest.fixture
def llama_index(ddtrace_global_config, ddtrace_config_llama_index):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        with override_config("llama_index", ddtrace_config_llama_index):
            with override_env(
                dict(
                    OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "<not-a-real-key>"),
                )
            ):
                patch()
                import llama_index

                yield llama_index
                unpatch()


@pytest.fixture(scope="session")
def request_vcr():
    yield get_request_vcr()
