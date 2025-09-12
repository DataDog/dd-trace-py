import logbook
from logbook import TestHandler
import pytest

from ddtrace import config
from ddtrace.constants import ENV_KEY
from ddtrace.constants import SERVICE_KEY
from ddtrace.constants import VERSION_KEY
from ddtrace.contrib.internal.logbook.patch import patch
from ddtrace.contrib.internal.logbook.patch import unpatch
from ddtrace.internal.constants import MAX_UINT_64BITS
from ddtrace.trace import tracer
from tests.utils import override_global_config


global_handler = TestHandler()


def _test_logging(span, env, service, version, handler=global_handler):
    dd_trace_id, dd_span_id = (span.trace_id, span.span_id) if span else (0, 0)

    if dd_trace_id > MAX_UINT_64BITS:
        dd_trace_id = "{:032x}".format(dd_trace_id)

    assert handler.records[0].message == "Hello!"
    assert handler.records[0].extra["dd.trace_id"] == str(dd_trace_id)
    assert handler.records[0].extra["dd.span_id"] == str(dd_span_id)
    assert handler.records[0].extra["dd.env"] == env or ""
    assert handler.records[0].extra["dd.service"] == service or ""
    assert handler.records[0].extra["dd.version"] == version or ""


@pytest.fixture(autouse=True)
def patch_logbook():
    try:
        patch()
        global_handler.push_application()
        yield
    finally:
        unpatch()


@pytest.fixture(autouse=True)
def global_config():
    with override_global_config({"service": "logging", "env": "global.env", "version": "global.version"}):
        yield
    global_handler.records.clear()


@pytest.mark.subprocess(
    ddtrace_run=True,
    parametrize=dict(DD_LOGS_INJECTION=["true", None]),
    env=dict(DD_SERVICE="dds", DD_ENV="ddenv", DD_VERSION="vv"),
    out=None,
    err=None,
)
def test_log_injection_enabled():
    import logbook

    from ddtrace import tracer
    from tests.contrib.logbook.test_logbook_logging import _test_logging

    handler = logbook.TestHandler()
    handler.push_application()

    with tracer.trace("test.logging") as span:
        logbook.info("Hello!")

    _test_logging(span, "ddenv", "dds", "vv", handler)


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_SERVICE="dds", DD_ENV="ddenv", DD_VERSION="vv", DD_LOGS_INJECTION="false"),
    out=None,
    err=None,
)
def test_log_injection_disabled():
    import logbook

    from ddtrace import tracer

    handler = logbook.TestHandler()
    handler.push_application()

    with tracer.trace("test.logging"):
        logbook.info("Hello!")

    for record in handler.records:
        assert (
            "dd.trace_id",
            "dd.span_id",
            "dd.service",
            "dd.env",
            "dd.version",
        ) not in record.extra, (
            f"Trace info should not be injected when DD_LOGS_INJECTION is false record: {record.extra}"
        )


def test_log_trace_global_values():
    """
    Check trace info includes global values over local span values
    """
    span = tracer.trace("test.logging")
    span.set_tag(ENV_KEY, "local-env")
    span.set_tag(SERVICE_KEY, "local-service")
    span.set_tag(VERSION_KEY, "local-version")

    logbook.info("Hello!")
    span.finish()
    _test_logging(span, config.env, config.service, config.version)


def test_log_no_trace():
    logbook.info("Hello!")

    _test_logging(None, config.env, config.service, config.version)


@pytest.mark.subprocess(env=dict(DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="False"))
def test_log_trace():
    """
    Check logging patched and formatter including trace info when 64bit trace ids are generated.
    """

    import logbook
    from logbook import TestHandler

    from ddtrace import config
    from ddtrace.contrib.internal.logbook.patch import patch
    from ddtrace.contrib.internal.logbook.patch import unpatch
    from ddtrace.trace import tracer

    config.service = "logging"
    config.env = "global.env"
    config.version = "global.version"

    handler = TestHandler()

    patch()
    handler.push_application()

    span = tracer.trace("test.logging")
    logbook.info("Hello!")
    span.finish()

    assert handler.records[0].message == "Hello!"
    assert handler.records[0].extra["dd.trace_id"] == str(span.trace_id)
    assert handler.records[0].extra["dd.span_id"] == str(span.span_id)
    assert handler.records[0].extra["dd.env"] == config.env
    assert handler.records[0].extra["dd.service"] == config.service
    assert handler.records[0].extra["dd.version"] == config.version

    handler.records.clear()
    unpatch()


@pytest.mark.subprocess(env=dict(DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="True"))
def test_log_trace_128bit_trace_ids():
    """
    Check if 128bit trace ids are logged using hex
    """

    import logbook
    from logbook import TestHandler

    from ddtrace import config
    from ddtrace.contrib.internal.logbook.patch import patch
    from ddtrace.contrib.internal.logbook.patch import unpatch
    from ddtrace.internal.constants import MAX_UINT_64BITS
    from ddtrace.trace import tracer

    config.service = "logging"
    config.env = "global.env"
    config.version = "global.version"

    handler = TestHandler()

    patch()
    handler.push_application()

    span = tracer.trace("test.logging")
    logbook.info("Hello!")
    span.finish()

    assert span.trace_id > MAX_UINT_64BITS
    assert handler.records[0].message == "Hello!"
    assert handler.records[0].extra["dd.trace_id"] == "{:032x}".format(span.trace_id)
    assert handler.records[0].extra["dd.span_id"] == str(span.span_id)
    assert handler.records[0].extra["dd.env"] == config.env
    assert handler.records[0].extra["dd.service"] == config.service
    assert handler.records[0].extra["dd.version"] == config.version

    handler.records.clear()
    unpatch()


@pytest.mark.subprocess(env=dict(DD_TAGS="service:ddtagservice,env:ddenv,version:ddversion"))
def test_log_DD_TAGS():
    import logbook
    from logbook import TestHandler

    from ddtrace.contrib.internal.logbook.patch import patch
    from ddtrace.contrib.internal.logbook.patch import unpatch
    from ddtrace.internal.constants import MAX_UINT_64BITS
    from ddtrace.trace import tracer

    handler = TestHandler()

    patch()
    handler.push_application()

    span = tracer.trace("test.logging")
    logbook.info("Hello!")
    span.finish()

    trace_id = span.trace_id
    if span.trace_id > MAX_UINT_64BITS:
        trace_id = "{:032x}".format(span.trace_id)

    assert handler.records[0].message == "Hello!"
    assert handler.records[0].extra["dd.trace_id"] == str(trace_id)
    assert handler.records[0].extra["dd.span_id"] == str(span.span_id)
    assert handler.records[0].extra["dd.env"] == "ddenv"
    assert handler.records[0].extra["dd.service"] == "ddtagservice"
    assert handler.records[0].extra["dd.version"] == "ddversion"

    handler.records.clear()
    unpatch()
