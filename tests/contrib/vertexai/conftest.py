import mock
from mock import PropertyMock
import pytest

from ddtrace.contrib.internal.vertexai.patch import patch
from ddtrace.contrib.internal.vertexai.patch import unpatch
from ddtrace.llmobs import LLMObs
from tests.contrib.vertexai.utils import MockAsyncPredictionServiceClient
from tests.contrib.vertexai.utils import MockPredictionServiceClient
from tests.utils import override_config
from tests.utils import override_global_config


def default_global_config():
    return {}


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def ddtrace_config_vertexai():
    return {}


@pytest.fixture
def mock_client():
    yield MockPredictionServiceClient()


@pytest.fixture
def mock_async_client():
    yield MockAsyncPredictionServiceClient()


@pytest.fixture
def test_spans(ddtrace_global_config, vertexai, test_spans, monkeypatch):
    try:
        if ddtrace_global_config.get("_llmobs_enabled", False):
            # Preserve meta_struct["_llmobs"] on spans so tests can assert against
            # LLMObsSpanData via _get_llmobs_data_metastruct; production scrubs it
            # after enqueueing to LLMObsSpanWriter.
            monkeypatch.setenv("_DD_LLMOBS_TEST_KEEP_META_STRUCT", "1")
            # Have to disable and re-enable LLMObs to use the mock tracer.
            LLMObs.disable()
            LLMObs.enable(integrations_enabled=False, agentless_enabled=False)
            # Replace the real LLMObsSpanWriter with a mock so we don't keep a
            # background flush thread alive trying to ship spans during the test.
            LLMObs._instance._llmobs_span_writer.stop()
            LLMObs._instance._llmobs_span_writer = mock.MagicMock()
        yield test_spans
    except Exception:
        yield


@pytest.fixture
def vertexai(ddtrace_global_config, ddtrace_config_vertexai, mock_client, mock_async_client):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        with override_config("vertexai", ddtrace_config_vertexai):
            patch()
            import vertexai
            from vertexai.generative_models import GenerativeModel

            with (
                mock.patch.object(
                    GenerativeModel, "_prediction_client", new_callable=PropertyMock
                ) as mock_client_property,
                mock.patch.object(
                    GenerativeModel, "_prediction_async_client", new_callable=PropertyMock
                ) as mock_async_client_property,
            ):
                mock_client_property.return_value = mock_client
                mock_async_client_property.return_value = mock_async_client
                yield vertexai

            unpatch()
