import pytest
import structlog

from ddtrace import Tracer
from ddtrace import config
from ddtrace import tracer
from ddtrace._trace.context import Context
from ddtrace._trace.provider import _DD_CONTEXTVAR
from ddtrace.opentracer.tracer import Tracer as OT_Tracer
from tests.utils import override_global_config


@pytest.fixture
def global_config():
    config.service = "test-service"
    config.env = "test-env"
    config.version = "test-version"
    global tracer
    tracer = Tracer()
    yield
    config.service = config.env = config.version = None


def tracer_injection(logger, log_method, event_dict):
    """Inject tracer information into custom structlog log"""
    correlation_log_context = tracer.get_log_correlation_context()
    # add ids and configs to structlog event dictionary
    event_dict["dd"] = correlation_log_context
    return event_dict


def format_trace_id(span):
    if config._128_bit_trace_id_enabled:
        return "{:032x}".format(span.trace_id)
    else:
        return str(span._trace_id_64bits)


class TestCorrelationLogsContext(object):
    def test_get_log_correlation_service(self, global_config):
        """Ensure expected DDLogRecord service is generated via get_correlation_log_record."""
        with tracer.trace("test-span-1", service="span-service") as span1:
            dd_log_record = tracer.get_log_correlation_context()
        assert dd_log_record == {
            "span_id": str(span1.span_id),
            "trace_id": format_trace_id(span1),
            "service": "span-service",
            "env": "test-env",
            "version": "test-version",
        }

        test_tracer = Tracer()
        with test_tracer.trace("test-span-2", service="span-service") as span2:
            dd_log_record = test_tracer.get_log_correlation_context()
        assert dd_log_record == {
            "span_id": str(span2.span_id),
            "trace_id": format_trace_id(span2),
            "service": "span-service",
            "env": "test-env",
            "version": "test-version",
        }

    def test_get_log_correlation_context_basic(self, global_config):
        """Ensure expected DDLogRecord is generated via get_correlation_log_record."""
        tracer = Tracer()
        with tracer.trace("test-span-1") as span1:
            dd_log_record = tracer.get_log_correlation_context()
        assert dd_log_record == {
            "span_id": str(span1.span_id),
            "trace_id": format_trace_id(span1),
            "service": "test-service",
            "env": "test-env",
            "version": "test-version",
        }
        test_tracer = Tracer()
        with test_tracer.trace("test-span-2") as span2:
            dd_log_record = test_tracer.get_log_correlation_context()
        assert dd_log_record == {
            "span_id": str(span2.span_id),
            "trace_id": format_trace_id(span2),
            "service": "test-service",
            "env": "test-env",
            "version": "test-version",
        }

        tracer.context_provider.activate(
            Context(
                span_id=234,
                trace_id=4321,
            )
        )
        assert test_tracer.get_log_correlation_context() == {
            "span_id": "234",
            "trace_id": "4321",
            "service": "test-service",
            "env": "test-env",
            "version": "test-version",
        }

    def test_get_log_correlation_context_opentracer(self, global_config):
        """Ensure expected DDLogRecord generated via get_correlation_log_record with an opentracing Tracer."""
        ot_tracer = OT_Tracer(service_name="test-service")
        with ot_tracer.start_active_span("operation") as scope:
            dd_span = scope._span._dd_span
            dd_log_record = ot_tracer.get_log_correlation_context()
        assert dd_log_record == {
            "span_id": str(dd_span.span_id),
            "trace_id": format_trace_id(dd_span),
            "service": "test-service",
            "env": "test-env",
            "version": "test-version",
        }

    def test_get_log_correlation_context_no_active_span(self):
        """Ensure empty DDLogRecord generated if no active span."""
        tracer = Tracer()
        dd_log_record = tracer.get_log_correlation_context()
        assert dd_log_record == {
            "span_id": "0",
            "trace_id": "0",
            "service": "",
            "env": "",
            "version": "",
        }

    def test_get_log_correlation_context_disabled_tracer(self):
        """Ensure get_correlation_log_record returns None if tracer is disabled."""
        tracer = Tracer()
        tracer.enabled = False
        with tracer.trace("test-span"):
            dd_log_record = tracer.get_log_correlation_context()
        assert dd_log_record == {
            "span_id": "0",
            "trace_id": "0",
            "service": "",
            "env": "",
            "version": "",
        }

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
        assert dd_log_record == {
            "span_id": str(span.span_id),
            "trace_id": format_trace_id(span),
            "service": "",
            "env": "",
            "version": "",
        }

    def test_custom_logging_injection_global_config(self):
        """Ensure custom log injection via get_correlation_log_record returns proper tracer information."""
        _DD_CONTEXTVAR.set(None)
        capture_log = structlog.testing.LogCapture()
        structlog.configure(processors=[tracer_injection, capture_log, structlog.processors.JSONRenderer()])
        logger = structlog.get_logger()

        with override_global_config(dict(version="global-version", env="global-env", service="global-service")):
            with tracer.trace("test span") as span:
                logger.msg("Hello!")

        assert len(capture_log.entries) == 1
        assert capture_log.entries[0]["event"] == "Hello!"
        dd_log_record = capture_log.entries[0]["dd"]
        assert dd_log_record == {
            "span_id": str(span.span_id),
            "trace_id": format_trace_id(span),
            "service": "global-service",
            "env": "global-env",
            "version": "global-version",
        }

    def test_custom_logging_injection_no_span(self):
        """Ensure custom log injection via get_correlation_log_record with no active span returns empty record."""
        _DD_CONTEXTVAR.set(None)
        capture_log = structlog.testing.LogCapture()
        structlog.configure(processors=[tracer_injection, capture_log, structlog.processors.JSONRenderer()])
        logger = structlog.get_logger()

        with override_global_config(dict(version="global-version", env="global-env", service="global-service")):
            logger.msg("No Span!")

        assert len(capture_log.entries) == 1
        assert capture_log.entries[0]["event"] == "No Span!"
        dd_log_record = capture_log.entries[0]["dd"]
        assert dd_log_record == {
            "span_id": "0",
            "trace_id": "0",
            "service": "global-service",
            "env": "global-env",
            "version": "global-version",
        }
