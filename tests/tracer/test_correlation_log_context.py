import pytest

from ddtrace import config
from ddtrace.trace import tracer
from tests.utils import DummyTracer


def global_config(config):
    config.service = "test-service"
    config.env = "test-env"
    config.version = "test-version"
    global tracer
    tracer = DummyTracer()
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


@pytest.mark.subprocess()
def test_get_log_correlation_service():
    """Ensure expected DDLogRecord service is generated via get_correlation_log_record."""
    from ddtrace.trace import tracer
    from tests.tracer.test_correlation_log_context import format_trace_id
    from tests.utils import DummyTracer
    from tests.utils import override_global_config

    with override_global_config(dict(service="test-service", env="test-env", version="test-version")):
        with tracer.trace("test-span-1", service="span-service") as span1:
            dd_log_record = tracer.get_log_correlation_context()
        assert dd_log_record == {
            "span_id": str(span1.span_id),
            "trace_id": format_trace_id(span1),
            "service": "span-service",
            "env": "test-env",
            "version": "test-version",
        }

        test_tracer = DummyTracer()
        with test_tracer.trace("test-span-2", service="span-service") as span2:
            dd_log_record = test_tracer.get_log_correlation_context()
        assert dd_log_record == {
            "span_id": str(span2.span_id),
            "trace_id": format_trace_id(span2),
            "service": "span-service",
            "env": "test-env",
            "version": "test-version",
        }


@pytest.mark.subprocess()
def test_get_log_correlation_context_basic():
    """Ensure expected DDLogRecord is generated via get_correlation_log_record."""
    from ddtrace.trace import Context
    from tests.tracer.test_correlation_log_context import format_trace_id
    from tests.utils import DummyTracer
    from tests.utils import override_global_config

    with override_global_config(dict(service="test-service", env="test-env", version="test-version")):
        tracer = DummyTracer()
        with tracer.trace("test-span-1") as span1:
            dd_log_record = tracer.get_log_correlation_context()
        assert dd_log_record == {
            "span_id": str(span1.span_id),
            "trace_id": format_trace_id(span1),
            "service": "test-service",
            "env": "test-env",
            "version": "test-version",
        }, dd_log_record
        test_tracer = DummyTracer()
        with test_tracer.trace("test-span-2") as span2:
            dd_log_record = test_tracer.get_log_correlation_context()
        assert dd_log_record == {
            "span_id": str(span2.span_id),
            "trace_id": format_trace_id(span2),
            "service": "test-service",
            "env": "test-env",
            "version": "test-version",
        }, dd_log_record

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
        }, test_tracer.get_log_correlation_context()


@pytest.mark.subprocess()
def test_get_log_correlation_context_opentracer():
    """Ensure expected DDLogRecord generated via get_correlation_log_record with an opentracing Tracer."""
    from ddtrace.opentracer.tracer import Tracer as OT_Tracer
    from tests.tracer.test_correlation_log_context import format_trace_id
    from tests.utils import override_global_config

    with override_global_config(dict(service="test-service", env="test-env", version="test-version")):
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
        }, dd_log_record


@pytest.mark.subprocess()
def test_get_log_correlation_context_no_active_span():
    """Ensure empty DDLogRecord generated if no active span."""
    from tests.utils import DummyTracer

    tracer = DummyTracer()
    dd_log_record = tracer.get_log_correlation_context()
    assert dd_log_record == {
        "span_id": "0",
        "trace_id": "0",
        "service": "ddtrace_subprocess_dir",
        "env": "",
        "version": "",
    }, dd_log_record


@pytest.mark.subprocess()
def test_get_log_correlation_context_disabled_tracer():
    """Ensure get_correlation_log_record returns None if tracer is disabled."""
    from ddtrace.trace import tracer

    tracer.enabled = False
    with tracer.trace("test-span"):
        dd_log_record = tracer.get_log_correlation_context()
    assert dd_log_record == {
        "span_id": "0",
        "trace_id": "0",
        "service": "ddtrace_subprocess_dir",
        "env": "",
        "version": "",
    }, dd_log_record


@pytest.mark.subprocess()
def test_custom_logging_injection_global_config():
    """Ensure custom log injection via get_correlation_log_record returns proper tracer information."""
    from ddtrace._trace.provider import _DD_CONTEXTVAR
    from ddtrace.contrib.internal.structlog.patch import patch
    from ddtrace.trace import tracer
    from tests.tracer.test_correlation_log_context import format_trace_id
    from tests.tracer.test_correlation_log_context import tracer_injection
    from tests.utils import override_global_config

    patch()

    import structlog

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
    }, dd_log_record


@pytest.mark.subprocess()
def test_custom_logging_injection_no_span():
    """Ensure custom log injection via get_correlation_log_record with no active span returns empty record."""
    from ddtrace._trace.provider import _DD_CONTEXTVAR
    from ddtrace.contrib.internal.structlog.patch import patch
    from tests.tracer.test_correlation_log_context import tracer_injection
    from tests.utils import override_global_config

    patch()

    import structlog

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
    }, dd_log_record


@pytest.mark.subprocess()
def test_custom_logging_injection():
    """Ensure custom log injection via get_correlation_log_record returns proper active span information."""
    from ddtrace.contrib.internal.structlog.patch import patch
    from ddtrace.trace import tracer
    from tests.tracer.test_correlation_log_context import format_trace_id
    from tests.tracer.test_correlation_log_context import tracer_injection

    patch()

    import structlog

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
        "service": "ddtrace_subprocess_dir",
        "env": "",
        "version": "",
    }, dd_log_record
