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
    event_dict["dd.trace_id"] = correlation_log_context.trace_id
    event_dict["dd.span_id"] = correlation_log_context.span_id
    event_dict["dd.env"] = correlation_log_context.env
    event_dict["dd.service"] = correlation_log_context.service
    event_dict["dd.version"] = correlation_log_context.version

    return event_dict


class TestCorrelationLogsContext(object):
    def test_get_log_correlation_context(self, global_config):
        """Ensure expected DDLogRecord is generated via get_correlation_log_record."""
        with tracer.trace("test-span-1") as span1:
            dd_log_record = tracer.get_log_correlation_context()
        assert dd_log_record.span_id == str(span1.span_id)
        assert dd_log_record.trace_id == str(span1.trace_id)
        assert dd_log_record.service == "test-service"
        assert dd_log_record.env == "test-env"
        assert dd_log_record.version == "test-version"

        test_tracer = Tracer()
        with test_tracer.trace("test-span-2") as span2:
            dd_log_record = test_tracer.get_log_correlation_context()
        assert dd_log_record.span_id == str(span2.span_id)
        assert dd_log_record.trace_id == str(span2.trace_id)
        assert dd_log_record.service == "test-service"
        assert dd_log_record.env == "test-env"
        assert dd_log_record.version == "test-version"

    def test_get_log_correlation_context_opentracer(self, global_config):
        """Ensure expected DDLogRecord generated via get_correlation_log_record with an opentracing Tracer."""
        ot_tracer = OT_Tracer()
        with ot_tracer.start_active_span("operation") as scope:
            dd_span = scope._span._dd_span
            dd_log_record = ot_tracer.get_log_correlation_context()
        assert dd_log_record.span_id == str(dd_span.span_id)
        assert dd_log_record.trace_id == str(dd_span.trace_id)
        assert dd_log_record.service == "test-service"
        assert dd_log_record.env == "test-env"
        assert dd_log_record.version == "test-version"

    def test_get_log_correlation_context_no_active_span(self):
        """Ensure empty DDLogRecord generated if no active span."""
        dd_log_record = tracer.get_log_correlation_context()
        assert dd_log_record.span_id == "0"
        assert dd_log_record.trace_id == "0"
        assert dd_log_record.service == ""
        assert dd_log_record.env == ""
        assert dd_log_record.version == ""

    def test_get_log_correlation_context_disabled_tracer(self):
        """Ensure get_correlation_log_record returns None if tracer is disabled."""
        tracer = Tracer()
        tracer.enabled = False
        with tracer.trace("test-span"):
            dd_log_record = tracer.get_log_correlation_context()
        assert dd_log_record is None

    def test_custom_logging_injection(self):
        """Ensure custom log injection via get_correlation_log_record returns proper active span information."""
        capture_log = structlog.testing.LogCapture()
        structlog.configure(processors=[tracer_injection, capture_log, structlog.processors.JSONRenderer()])
        logger = structlog.get_logger()

        with tracer.trace("test span") as span:
            logger.msg("Hello!")

        assert len(capture_log.entries) == 1
        assert capture_log.entries[0]["event"] == "Hello!"
        assert capture_log.entries[0]["dd.trace_id"] == str(span.trace_id)
        assert capture_log.entries[0]["dd.span_id"] == str(span.span_id)
        assert capture_log.entries[0]["dd.version"] == ""
        assert capture_log.entries[0]["dd.env"] == ""
        assert capture_log.entries[0]["dd.service"] == ""

    def test_custom_logging_injection_global_config(self):
        """Ensure custom log injection via get_correlation_log_record returns proper tracer information."""
        capture_log = structlog.testing.LogCapture()
        structlog.configure(processors=[tracer_injection, capture_log, structlog.processors.JSONRenderer()])
        logger = structlog.get_logger()

        with override_global_config(dict(version="global.version", env="global.env", service="global.service")):
            with tracer.trace("test span") as span:
                logger.msg("Hello!")

        assert len(capture_log.entries) == 1
        assert capture_log.entries[0]["event"] == "Hello!"
        assert capture_log.entries[0]["dd.trace_id"] == str(span.trace_id)
        assert capture_log.entries[0]["dd.span_id"] == str(span.span_id)
        assert capture_log.entries[0]["dd.version"] == "global.version"
        assert capture_log.entries[0]["dd.env"] == "global.env"
        assert capture_log.entries[0]["dd.service"] == "global.service"

    def test_custom_logging_injection_no_span(self):
        """Ensure custom log injection via get_correlation_log_record with no active span returns empty record."""
        capture_log = structlog.testing.LogCapture()
        structlog.configure(processors=[tracer_injection, capture_log, structlog.processors.JSONRenderer()])
        logger = structlog.get_logger()

        with override_global_config(dict(version="global.version", env="global.env", service="global.service")):
            logger.msg("No Span!")

        assert len(capture_log.entries) == 1
        assert capture_log.entries[0]["event"] == "No Span!"
        assert capture_log.entries[0]["dd.trace_id"] == "0"
        assert capture_log.entries[0]["dd.span_id"] == "0"
        assert capture_log.entries[0]["dd.version"] == "global.version"
        assert capture_log.entries[0]["dd.env"] == "global.env"
        assert capture_log.entries[0]["dd.service"] == "global.service"
