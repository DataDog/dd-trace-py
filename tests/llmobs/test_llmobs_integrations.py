import mock
import pytest

from ddtrace.llmobs._integrations import BaseLLMIntegration
from tests.utils import DummyTracer


@pytest.fixture(scope="function")
def mock_integration_config(ddtrace_global_config):
    mock_config = mock.Mock()
    mock_config.metrics_enabled = True
    mock_config.span_char_limit = 10
    mock_config.span_prompt_completion_sample_rate = 1.0
    mock_config.log_prompt_completion_sample_rate = 1.0
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


def test_integration_metrics_enabled(mock_integration_config, ddtrace_global_config, monkeypatch):
    """Metrics should only ever be submitted if not in agentless mode, and if metrics env var is set to true."""
    monkeypatch.setenv("DD_BASELLM_METRICS_ENABLED", "1")
    integration = BaseLLMIntegration(mock_integration_config)
    assert integration.metrics_enabled is True

    monkeypatch.setenv("DD_BASELLM_METRICS_ENABLED", "1")
    ddtrace_global_config._llmobs_agentless_enabled = True
    integration = BaseLLMIntegration(mock_integration_config)
    assert integration.metrics_enabled is False

    monkeypatch.setenv("DD_BASELLM_METRICS_ENABLED", "0")
    mock_integration_config.metrics_enabled = False
    ddtrace_global_config._llmobs_agentless_enabled = True
    integration = BaseLLMIntegration(mock_integration_config)
    assert integration.metrics_enabled is False

    monkeypatch.setenv("DD_BASELLM_METRICS_ENABLED", "0")
    mock_integration_config.metrics_enabled = False
    ddtrace_global_config._llmobs_agentless_enabled = False
    integration = BaseLLMIntegration(mock_integration_config)
    assert integration.metrics_enabled is False


def test_integration_logs_enabled(mock_integration_config):
    mock_integration_config.logs_enabled = False
    integration = BaseLLMIntegration(mock_integration_config)
    assert integration.logs_enabled is False

    mock_integration_config.logs_enabled = True
    integration = BaseLLMIntegration(mock_integration_config)
    assert integration.logs_enabled is True


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


def test_pc_span_sampling_log(mock_integration_config, mock_pin):
    mock_integration_config.logs_enabled = True
    integration = BaseLLMIntegration(mock_integration_config)
    integration.pin = mock_pin
    with mock_pin.tracer.trace("Dummy span") as mock_span:
        assert integration.is_pc_sampled_log(mock_span) is True

    mock_integration_config.log_prompt_completion_sample_rate = 0.0
    mock_integration_config.logs_enabled = False
    integration = BaseLLMIntegration(mock_integration_config)
    integration.pin = mock_pin
    with mock_pin.tracer.trace("Dummy span") as mock_span:
        assert integration.is_pc_sampled_log(mock_span) is False


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


@mock.patch("ddtrace.llmobs._integrations.base.V2LogWriter")
def test_log_writer_started(mock_log_writer, mock_integration_config):
    mock_integration_config.logs_enabled = True
    _ = BaseLLMIntegration(mock_integration_config)
    mock_log_writer().start.assert_called_once()

    mock_log_writer.reset_mock()
    mock_integration_config.logs_enabled = False
    _ = BaseLLMIntegration(mock_integration_config)
    mock_log_writer().start.assert_not_called()


@mock.patch("ddtrace.llmobs._integrations.base.V2LogWriter")
def test_log_enqueues_log_writer(mock_log_writer, mock_integration_config):
    span = DummyTracer().trace("Dummy span", service="dummy_service")
    mock_integration_config.logs_enabled = True
    integration = BaseLLMIntegration(mock_integration_config)
    integration.log(span, "level", "message", {"DummyKey": "DummyValue"})
    mock_log_writer().enqueue.assert_called_once_with(
        {
            "timestamp": mock.ANY,
            "message": "message",
            "hostname": mock.ANY,
            "ddsource": "baseLLM",
            "service": "dummy_service",
            "status": "level",
            "ddtags": mock.ANY,
            "dd.span_id": str(span.span_id),
            "dd.trace_id": "{:x}".format(span.trace_id),
            "DummyKey": "DummyValue",
        }
    )


def test_metric_calls_statsd_client(mock_integration_config, monkeypatch):
    monkeypatch.setenv("DD_BASELLM_METRICS_ENABLED", "1")
    span = DummyTracer().trace("Dummy span", service="dummy_service")
    integration = BaseLLMIntegration(mock_integration_config)
    mock_statsd = mock.Mock()
    integration._statsd = mock_statsd

    integration.metric(span, "dist", "name", 1, tags=["tag:value"])
    mock_statsd.distribution.assert_called_once_with("name", 1, tags=["tag:value"])

    integration.metric(span, "incr", "name", 2, tags=["tag:value"])
    mock_statsd.increment.assert_called_once_with("name", 2, tags=["tag:value"])

    integration.metric(span, "gauge", "name", 3, tags=["tag:value"])
    mock_statsd.gauge.assert_called_once_with("name", 3, tags=["tag:value"])

    with pytest.raises(ValueError):
        integration.metric(span, "metric_type_does_not_exist", "name", 3, tags=["tag:value"])


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
