from unittest import mock

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
def test_spans(ddtrace_global_config, vertexai, test_spans):
    try:
        if ddtrace_global_config.get("_llmobs_enabled", False):
            # Have to disable and re-enable LLMObs to use the mock tracer.
            LLMObs.disable()
            LLMObs.enable(integrations_enabled=False, agentless_enabled=False)
        yield test_spans
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
