import mock
import pytest

from ddtrace.llmobs._integrations import BaseLLMIntegration
from tests.utils import DummyTracer


@pytest.fixture(scope="function")
def mock_integration_config(ddtrace_global_config):
    mock_config = mock.Mock()
    mock_config.span_char_limit = 10
    mock_config.span_prompt_completion_sample_rate = 1.0
    yield mock_config


@pytest.fixture(scope="function")
def ddtrace_global_config():
    with mock.patch("ddtrace.llmobs._integrations.base.config") as mock_global_config:
        mock_global_config._llmobs_agentless_enabled = False
        mock_global_config._llmobs_sample_rate = 1.0
        mock_global_config._dd_api_key = "<not-a-real-key>"
        yield mock_global_config


@pytest.fixture(scope="function")
def mock_pin():
    mock_pin = mock.Mock()
    mock_pin.tracer = DummyTracer()
    mock_pin.service = "dummy_service"
    yield mock_pin


def test_integration_truncate(mock_integration_config):
    integration = BaseLLMIntegration(mock_integration_config)
    assert integration.trunc("1" * 128) == "1111111111..."
    assert integration.trunc("123") == "123"


@mock.patch("ddtrace.llmobs._integrations.base.LLMObs")
def test_integration_llmobs_enabled(mock_llmobs, mock_integration_config):
    mock_llmobs.enabled = True
    integration = BaseLLMIntegration(mock_integration_config)
    assert integration.llmobs_enabled is True
    mock_llmobs.enabled = False
    integration = BaseLLMIntegration(mock_integration_config)
    assert integration.llmobs_enabled is False


def test_pc_span_sampling(mock_integration_config, mock_pin):
    integration = BaseLLMIntegration(mock_integration_config)
    integration.pin = mock_pin
    with mock_pin.tracer.trace("Dummy span") as mock_span:
        assert integration.is_pc_sampled_span(mock_span) is True

    mock_integration_config.span_prompt_completion_sample_rate = 0.0
    integration = BaseLLMIntegration(mock_integration_config)
    integration.pin = mock_pin
    with mock_pin.tracer.trace("Dummy span") as mock_span:
        assert integration.is_pc_sampled_span(mock_span) is False


@mock.patch("ddtrace.llmobs._integrations.base.LLMObs")
def test_pc_span_sampling_llmobs(mock_llmobs, mock_integration_config, mock_pin):
    mock_llmobs.enabled = True
    integration = BaseLLMIntegration(mock_integration_config)
    integration.pin = mock_pin
    with mock_pin.tracer.trace("Dummy span") as mock_span:
        assert integration.is_pc_sampled_llmobs(mock_span) is True
    mock_llmobs.enabled = False
    integration = BaseLLMIntegration(mock_integration_config)
    integration.pin = mock_pin
    with mock_pin.tracer.trace("Dummy span") as mock_span:
        assert integration.is_pc_sampled_llmobs(mock_span) is False


def test_integration_trace(mock_integration_config, mock_pin):
    integration = BaseLLMIntegration(mock_integration_config)
    mock_set_base_span_tags = mock.Mock()
    integration._set_base_span_tags = mock_set_base_span_tags
    integration.pin = mock_pin
    with integration.trace(mock_pin, "dummy_operation_id"):
        pass
    span = mock_pin.tracer.pop()
    assert span is not None
    assert span[0].resource == "dummy_operation_id"
    assert span[0].service == "dummy_service"
    mock_set_base_span_tags.assert_called_once()


@mock.patch("ddtrace.llmobs._integrations.base.log")
@mock.patch("ddtrace.llmobs._integrations.base.LLMObs")
def test_llmobs_set_tags(mock_llmobs, mock_log, mock_integration_config):
    span = DummyTracer().trace("Dummy span", service="dummy_service")
    integration = BaseLLMIntegration(mock_integration_config)
    integration._llmobs_set_tags = mock.Mock()
    integration.llmobs_set_tags(span, args=[], kwargs={}, response="response", operation="operation")
    integration._llmobs_set_tags.assert_called_once_with(span, [], {}, "response", "operation")

    integration._llmobs_set_tags = mock.Mock(side_effect=AttributeError("Mocked Exception during _llmobs_set_tags()"))
    integration.llmobs_set_tags(
        span, args=[1, 2, 3], kwargs={"a": 123}, response=[{"content": "hello"}], operation="operation"
    )
    integration._llmobs_set_tags.assert_called_once_with(
        span, [1, 2, 3], {"a": 123}, [{"content": "hello"}], "operation"
    )
    mock_log.error.assert_called_once_with(
        "Error extracting LLMObs fields for span %s, likely due to malformed data", span, exc_info=True
    )
