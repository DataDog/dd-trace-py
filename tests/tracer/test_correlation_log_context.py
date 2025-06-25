import pytest


@pytest.mark.subprocess(
    ddtrace_run=True, env={"DD_VERSION": "test-version", "DD_ENV": "test-env", "DD_SERVICE": "test-service"}
)
def test_get_log_correlation_ust():
    """Ensure expected DDLogRecord service is generated via get_correlation_log_record."""
    from ddtrace.constants import ENV_KEY
    from ddtrace.constants import VERSION_KEY
    from ddtrace.internal.utils.formats import format_trace_id
    from ddtrace.trace import tracer

    with tracer.trace("test-span-1") as span1:
        dd_log_record = tracer.get_log_correlation_context()
        assert dd_log_record == {
            "dd.span_id": str(span1.span_id),
            "dd.trace_id": format_trace_id(span1.trace_id),
            "dd.service": "test-service",
            "dd.env": "test-env",
            "dd.version": "test-version",
        }, dd_log_record
    # Ensure that the USTs from the global config is used, not the span
    with tracer.trace("test-span-2", service="span-service") as span2:
        span2.set_tag(VERSION_KEY, "span-version")
        span2.set_tag(ENV_KEY, "span-env")
        dd_log_record = tracer.get_log_correlation_context()
        assert dd_log_record == {
            "dd.span_id": str(span2.span_id),
            "dd.trace_id": format_trace_id(span2.trace_id),
            "dd.service": "test-service",
            "dd.env": "test-env",
            "dd.version": "test-version",
        }, dd_log_record


@pytest.mark.subprocess(
    ddtrace_run=True, env={"DD_VERSION": "test-version", "DD_ENV": "test-env", "DD_SERVICE": "test-service"}
)
def test_get_log_correlation_trace_context():
    """Ensure expected DDLogRecord is generated via get_correlation_log_record."""
    from ddtrace.trace import Context
    from ddtrace.trace import tracer

    tracer.context_provider.activate(
        Context(
            span_id=234,
            trace_id=4321,
        )
    )
    dd_log_record = tracer.get_log_correlation_context()
    assert dd_log_record == {
        "dd.span_id": "234",
        "dd.trace_id": "4321",
        "dd.service": "test-service",
        "dd.env": "test-env",
        "dd.version": "test-version",
    }, dd_log_record


@pytest.mark.subprocess(
    ddtrace_run=True, env={"DD_VERSION": "test-version", "DD_ENV": "test-env", "DD_SERVICE": "test-service"}
)
def test_get_log_correlation_context_opentracer():
    """Ensure expected DDLogRecord generated via get_correlation_log_record with an opentracing Tracer."""
    from ddtrace.internal.utils.formats import format_trace_id
    from ddtrace.opentracer.tracer import Tracer as OT_Tracer

    ot_tracer = OT_Tracer(service_name="test-service")
    with ot_tracer.start_active_span("operation") as scope:
        dd_span = scope._span._dd_span
        dd_log_record = ot_tracer.get_log_correlation_context()
    assert dd_log_record == {
        "dd.span_id": str(dd_span.span_id),
        "dd.trace_id": format_trace_id(dd_span.trace_id),
        "dd.service": "test-service",
        "dd.env": "test-env",
        "dd.version": "test-version",
    }, dd_log_record


@pytest.mark.subprocess()
def test_get_log_correlation_context_no_active_span():
    """Ensure empty DDLogRecord generated if no active span."""
    from ddtrace.trace import tracer

    dd_log_record = tracer.get_log_correlation_context()
    assert dd_log_record == {
        "dd.span_id": "0",
        "dd.trace_id": "0",
        "dd.service": "ddtrace_subprocess_dir",
        "dd.env": "",
        "dd.version": "",
    }, dd_log_record


@pytest.mark.subprocess(env={"DD_VERSION": None, "DD_ENV": None, "DD_SERVICE": None, "DD_TRACE_ENABLED": "false"})
def test_get_log_correlation_context_disabled_tracer():
    """Ensure get_correlation_log_record returns None if tracer is disabled."""
    from ddtrace.trace import tracer

    with tracer.trace("test-span"):
        dd_log_record = tracer.get_log_correlation_context()
    assert dd_log_record == {
        "dd.span_id": "0",
        "dd.trace_id": "0",
        "dd.service": "ddtrace_subprocess_dir",
        "dd.env": "",
        "dd.version": "",
    }, dd_log_record


@pytest.mark.subprocess(ddtrace_run=True)
def test_structored_logging_injection():
    """Ensure the structored loggers automatically injects trace attributes into the
    log records when ddtrace_run is used.
    """
    import structlog

    from ddtrace import config
    from ddtrace.internal.utils.formats import format_trace_id
    from ddtrace.trace import tracer

    capture_log = structlog.testing.LogCapture()
    structlog.configure(processors=[capture_log, structlog.processors.JSONRenderer()])
    logger = structlog.get_logger()

    config.service = "global-service"
    config.env = "global-env"
    config.version = "global-version"
    with tracer.trace("test span") as span:
        logger.msg("Hello!")

    assert len(capture_log.entries) == 1
    assert capture_log.entries[0]["event"] == "Hello!"
    assert capture_log.entries[0] == {
        "event": "Hello!",
        "dd.span_id": str(span.span_id),
        "dd.trace_id": format_trace_id(span.trace_id),
        "dd.service": "global-service",
        "dd.env": "global-env",
        "dd.version": "global-version",
        "log_level": "info",
    }, capture_log.entries


@pytest.mark.subprocess(
    ddtrace_run=True, env={"DD_VERSION": "global-version", "DD_ENV": "global-env", "DD_SERVICE": "global-service"}
)
def test_structored_logging_injection_no_span():
    """Ensure the structored loggers automatically injects global config attributes into the log records."""
    import structlog

    capture_log = structlog.testing.LogCapture()
    structlog.configure(processors=[capture_log, structlog.processors.JSONRenderer()])
    logger = structlog.get_logger()
    logger.msg("No Span!")

    assert len(capture_log.entries) == 1
    assert capture_log.entries[0] == {
        "event": "No Span!",
        "dd.span_id": "0",
        "dd.trace_id": "0",
        "dd.service": "global-service",
        "dd.env": "global-env",
        "dd.version": "global-version",
        "log_level": "info",
    }, capture_log.entries[0]


@pytest.mark.subprocess(
    ddtrace_run=True, env={"DD_LOGS_INJECTION": None, "DD_VERSION": None, "DD_ENV": None, "DD_SERVICE": None}
)
def test_structored_logging_injection_default_configs():
    """Ensure the structored loggers automatically injects default trace attributes into the log records."""
    import structlog

    from ddtrace.internal.utils.formats import format_trace_id
    from ddtrace.trace import tracer

    capture_log = structlog.testing.LogCapture()
    structlog.configure(processors=[capture_log, structlog.processors.JSONRenderer()])
    logger = structlog.get_logger()

    with tracer.trace("test span") as span:
        logger.msg("Hello!")

    assert len(capture_log.entries) == 1
    assert capture_log.entries[0] == {
        "event": "Hello!",
        "dd.span_id": str(span.span_id),
        "dd.trace_id": format_trace_id(span.trace_id),
        "dd.service": "ddtrace_subprocess_dir",
        "dd.env": "",
        "dd.version": "",
        "log_level": "info",
    }, capture_log.entries
