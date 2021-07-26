import pytest
import structlog

from ddtrace import Tracer
from ddtrace import config
from ddtrace import tracer
from ddtrace.opentracer.tracer import Tracer as OT_Tracer
from tests.utils import override_global_config


@pytest.fixture
def global_config():
    config.service = "test-service"
    config.env = "test-env"
    config.version = "test-version"
    yield
    config.service = config.env = config.version = None


def tracer_injection(logger, log_method, event_dict):
    """Inject tracer information into custom structlog log"""
    correlation_log_context = tracer.get_log_correlation_context()
    # add ids and configs to structlog event dictionary
    event_dict["dd"] = correlation_log_context
    return event_dict


def assert_dd_log_record_matches_context(dd_log_record, span, service, env, version):
    """Assert generated log correlation dict matches the expected span/trace ID and service, env, and version names."""
    assert dd_log_record["span_id"] == str(span.span_id) if span else "0"
    assert dd_log_record["trace_id"] == str(span.trace_id) if span else "0"
    assert dd_log_record["service"] == service
    assert dd_log_record["env"] == env
    assert dd_log_record["version"] == version


class TestCorrelationLogsContext(object):
    def test_get_log_correlation_context(self, global_config):
        """Ensure expected DDLogRecord is generated via get_correlation_log_record."""
        with tracer.trace("test-span-1") as span1:
            dd_log_record = tracer.get_log_correlation_context()
        assert_dd_log_record_matches_context(dd_log_record, span1, "test-service", "test-env", "test-version")
        test_tracer = Tracer()
        with test_tracer.trace("test-span-2") as span2:
            dd_log_record = test_tracer.get_log_correlation_context()
        assert_dd_log_record_matches_context(dd_log_record, span2, "test-service", "test-env", "test-version")

    def test_get_log_correlation_context_opentracer(self, global_config):
        """Ensure expected DDLogRecord generated via get_correlation_log_record with an opentracing Tracer."""
        ot_tracer = OT_Tracer()
        with ot_tracer.start_active_span("operation") as scope:
            dd_span = scope._span._dd_span
            dd_log_record = ot_tracer.get_log_correlation_context()
        assert_dd_log_record_matches_context(dd_log_record, dd_span, "test-service", "test-env", "test-version")

    def test_get_log_correlation_context_no_active_span(self):
        """Ensure empty DDLogRecord generated if no active span."""
        dd_log_record = tracer.get_log_correlation_context()
        assert_dd_log_record_matches_context(dd_log_record, None, "", "", "")

    def test_get_log_correlation_context_disabled_tracer(self):
        """Ensure get_correlation_log_record returns None if tracer is disabled."""
        tracer = Tracer()
        tracer.enabled = False
        with tracer.trace("test-span"):
            dd_log_record = tracer.get_log_correlation_context()
        assert_dd_log_record_matches_context(dd_log_record, None, "", "", "")

    def test_custom_logging_injection(self):
        """Ensure custom log injection via get_correlation_log_record returns proper active span information."""
        capture_log = structlog.testing.LogCapture()
        structlog.configure(processors=[tracer_injection, capture_log, structlog.processors.JSONRenderer()])
        logger = structlog.get_logger()

        with tracer.trace("test span") as span:
            logger.msg("Hello!")

        assert len(capture_log.entries) == 1
        assert capture_log.entries[0]["event"] == "Hello!"
        dd_log_record = capture_log.entries[0]["dd"]
        assert_dd_log_record_matches_context(dd_log_record, span, "", "", "")

    def test_custom_logging_injection_global_config(self):
        """Ensure custom log injection via get_correlation_log_record returns proper tracer information."""
        capture_log = structlog.testing.LogCapture()
        structlog.configure(processors=[tracer_injection, capture_log, structlog.processors.JSONRenderer()])
        logger = structlog.get_logger()

        with override_global_config(dict(version="global-version", env="global-env", service="global-service")):
            with tracer.trace("test span") as span:
                logger.msg("Hello!")

        assert len(capture_log.entries) == 1
        assert capture_log.entries[0]["event"] == "Hello!"
        dd_log_record = capture_log.entries[0]["dd"]
        assert_dd_log_record_matches_context(dd_log_record, span, "global-service", "global-env", "global-version")

    def test_custom_logging_injection_no_span(self):
        """Ensure custom log injection via get_correlation_log_record with no active span returns empty record."""
        capture_log = structlog.testing.LogCapture()
        structlog.configure(processors=[tracer_injection, capture_log, structlog.processors.JSONRenderer()])
        logger = structlog.get_logger()

        with override_global_config(dict(version="global-version", env="global-env", service="global-service")):
            logger.msg("No Span!")

        assert len(capture_log.entries) == 1
        assert capture_log.entries[0]["event"] == "No Span!"
        dd_log_record = capture_log.entries[0]["dd"]
        assert_dd_log_record_matches_context(dd_log_record, None, "global-service", "global-env", "global-version")
