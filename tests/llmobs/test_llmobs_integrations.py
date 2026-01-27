import mock
import pytest

from ddtrace._trace.pin import Pin
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.llmobs._integrations import BaseLLMIntegration


@pytest.fixture(scope="function")
def mock_integration_config(ddtrace_global_config):
    myint = IntegrationConfig(ddtrace_global_config, "myint", service="dummy_service")
    ddtrace_global_config.myint = myint
    return myint


@pytest.fixture(scope="function")
def ddtrace_global_config():
    with mock.patch("ddtrace.llmobs._integrations.base.config") as mock_global_config:
        mock_global_config._llmobs_agentless_enabled = False
        mock_global_config._llmobs_sample_rate = 1.0
        mock_global_config._dd_api_key = "<not-a-real-key>"
        yield mock_global_config


@mock.patch("ddtrace.llmobs._integrations.base.LLMObs")
def test_integration_llmobs_enabled(mock_llmobs, mock_integration_config):
    mock_llmobs.enabled = True
    integration = BaseLLMIntegration(mock_integration_config)
    assert integration.llmobs_enabled is True
    mock_llmobs.enabled = False
    integration = BaseLLMIntegration(mock_integration_config)
    assert integration.llmobs_enabled is False


@mock.patch("ddtrace.llmobs._integrations.base.LLMObs")
def test_pc_span_sampling_llmobs(mock_llmobs, mock_integration_config, tracer):
    mock_llmobs.enabled = True
    integration = BaseLLMIntegration(mock_integration_config)
    with tracer.trace("Dummy span") as mock_span:
        assert integration.is_pc_sampled_llmobs(mock_span) is True
    mock_llmobs.enabled = False
    integration = BaseLLMIntegration(mock_integration_config)
    with tracer.trace("Dummy span") as mock_span:
        assert integration.is_pc_sampled_llmobs(mock_span) is False


def test_integration_trace(mock_integration_config, test_spans):
    integration = BaseLLMIntegration(mock_integration_config)
    mock_set_base_span_tags = mock.Mock()
    integration._set_base_span_tags = mock_set_base_span_tags

    with integration.trace(Pin(), "dummy_operation_id"):
        pass
    span = test_spans.pop()
    assert span is not None
    assert span[0].resource == "dummy_operation_id"
    assert span[0].service == "dummy_service"
    mock_set_base_span_tags.assert_called_once()


@mock.patch("ddtrace.llmobs._integrations.base.log")
@mock.patch("ddtrace.llmobs._integrations.base.LLMObs")
def test_llmobs_set_tags(mock_llmobs, mock_log, tracer, mock_integration_config):
    span = tracer.trace("Dummy span", service="dummy_service")
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
