from unittest import mock

import pytest

from ddtrace.contrib.internal.litellm.patch import patch
from ddtrace.contrib.internal.litellm.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.contrib.litellm.utils import get_request_vcr
from tests.contrib.litellm.utils import model_list
from tests.utils import override_global_config


def default_global_config():
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
                # Have to disable and re-enable LLMObs to use the mock tracer.
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


@pytest.fixture
def litellm(ddtrace_global_config, monkeypatch):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        monkeypatch.setenv("OPENAI_API_KEY", "<not-a-real-key>")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "<not-a-real-key>")
        monkeypatch.setenv("COHERE_API_KEY", "<not-a-real-key>")
        patch()
        import litellm

        yield litellm
        unpatch()


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
