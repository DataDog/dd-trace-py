import json

import pytest
import structlog

from ddtrace import config
from ddtrace import tracer
from ddtrace.constants import ENV_KEY
from ddtrace.constants import SERVICE_KEY
from ddtrace.constants import VERSION_KEY
from ddtrace.contrib.structlog import patch
from ddtrace.contrib.structlog import unpatch
from ddtrace.internal.constants import MAX_UINT_64BITS
from tests.utils import override_global_config


cf = structlog.testing.CapturingLoggerFactory()


def _test_logging(output, span, env, service, version):
    dd_trace_id, dd_span_id = (span.trace_id, span.span_id) if span else (0, 0)

    if dd_trace_id > MAX_UINT_64BITS:
        dd_trace_id = "{:032x}".format(dd_trace_id)

    assert json.loads(output[0].args[0])["event"] == "Hello!"
    assert json.loads(output[0].args[0])["dd.trace_id"] == str(dd_trace_id)
    assert json.loads(output[0].args[0])["dd.span_id"] == str(dd_span_id)
    assert json.loads(output[0].args[0])["dd.env"] == env or ""
    assert json.loads(output[0].args[0])["dd.service"] == service or ""
    assert json.loads(output[0].args[0])["dd.version"] == version or ""


@pytest.fixture(autouse=True)
def patch_structlog():
    try:
        patch()
        structlog.configure(
            processors=[structlog.processors.JSONRenderer()],
            logger_factory=cf,
        )
        yield
    finally:
        unpatch()


@pytest.fixture(autouse=True)
def global_config():
    with override_global_config({"service": "logging", "env": "global.env", "version": "global.version"}):
        yield
    cf.logger.calls.clear()


def test_log_trace_global_values():
    """
    Check trace info includes global values over local span values
    """
    span = tracer.trace("test.logging")
    span.set_tag(ENV_KEY, "local-env")
    span.set_tag(SERVICE_KEY, "local-service")
    span.set_tag(VERSION_KEY, "local-version")

    structlog.get_logger().info("Hello!")
    span.finish()

    output = cf.logger.calls

    _test_logging(output, span, config.env, config.service, config.version)


def test_log_no_trace():
    structlog.get_logger().info("Hello!")
    output = cf.logger.calls

    _test_logging(output, None, config.env, config.service, config.version)


@pytest.mark.subprocess(env=dict(DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="False"))
def test_log_trace():
    """
    Check logging patched and formatter including trace info when 64bit trace ids are generated.
    """

    import json

    import structlog

    from ddtrace import config
    from ddtrace import tracer
    from ddtrace.contrib.structlog import patch
    from ddtrace.contrib.structlog import unpatch

    config.service = "logging"
    config.env = "global.env"
    config.version = "global.version"

    patch()

    cf = structlog.testing.CapturingLoggerFactory()
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=cf,
    )
    logger = structlog.getLogger()

    span = tracer.trace("test.logging")
    logger.info("Hello!")
    span.finish()

    output = cf.logger.calls

    assert json.loads(output[0].args[0])["event"] == "Hello!"
    assert json.loads(output[0].args[0])["dd.trace_id"] == str(span.trace_id)
    assert json.loads(output[0].args[0])["dd.span_id"] == str(span.span_id)
    assert json.loads(output[0].args[0])["dd.env"] == "global.env"
    assert json.loads(output[0].args[0])["dd.service"] == "logging"
    assert json.loads(output[0].args[0])["dd.version"] == "global.version"

    cf.logger.calls.clear()
    unpatch()


@pytest.mark.subprocess(env=dict(DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="True"))
def test_log_trace_128bit_trace_ids():
    """
    Check if 128bit trace ids are logged in hex
    """

    import json

    import structlog

    from ddtrace import config
    from ddtrace import tracer
    from ddtrace.contrib.structlog import patch
    from ddtrace.contrib.structlog import unpatch
    from ddtrace.internal.constants import MAX_UINT_64BITS

    config.service = "logging"
    config.env = "global.env"
    config.version = "global.version"

    patch()

    cf = structlog.testing.CapturingLoggerFactory()
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=cf,
    )
    logger = structlog.getLogger()

    span = tracer.trace("test.logging")
    logger.info("Hello!")
    span.finish()

    assert span.trace_id > MAX_UINT_64BITS

    output = cf.logger.calls

    assert json.loads(output[0].args[0])["event"] == "Hello!"
    assert json.loads(output[0].args[0])["dd.trace_id"] == "{:032x}".format(span.trace_id)
    assert json.loads(output[0].args[0])["dd.span_id"] == str(span.span_id)
    assert json.loads(output[0].args[0])["dd.env"] == "global.env"
    assert json.loads(output[0].args[0])["dd.service"] == "logging"
    assert json.loads(output[0].args[0])["dd.version"] == "global.version"

    cf.logger.calls.clear()
    unpatch()


@pytest.mark.subprocess(env=dict(DD_TAGS="service:ddtagservice,env:ddenv,version:ddversion"))
def test_log_DD_TAGS():
    import json

    import structlog

    from ddtrace import tracer
    from ddtrace.constants import ENV_KEY
    from ddtrace.constants import SERVICE_KEY
    from ddtrace.constants import VERSION_KEY
    from ddtrace.contrib.structlog import patch
    from ddtrace.contrib.structlog import unpatch

    patch()

    cf = structlog.testing.CapturingLoggerFactory()
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=cf,
    )
    logger = structlog.getLogger()

    span = tracer.trace("test.logging")
    span.set_tag(ENV_KEY, "local-env")
    span.set_tag(SERVICE_KEY, "local-service")
    span.set_tag(VERSION_KEY, "local-version")

    logger.info("Hello!")
    span.finish()

    output = cf.logger.calls

    assert json.loads(output[0].args[0])["event"] == "Hello!"
    assert json.loads(output[0].args[0])["dd.trace_id"] == "{:032x}".format(span.trace_id)
    assert json.loads(output[0].args[0])["dd.span_id"] == str(span.span_id)
    assert json.loads(output[0].args[0])["dd.env"] == "ddenv"
    assert json.loads(output[0].args[0])["dd.service"] == "ddtagservice"
    assert json.loads(output[0].args[0])["dd.version"] == "ddversion"

    cf.logger.calls.clear()
    unpatch()


@pytest.mark.subprocess()
def test_tuple_processor_list():
    """
    Regression test for: https://github.com/DataDog/dd-trace-py/issues/7665
    """
    import json

    import structlog

    from ddtrace import config
    from ddtrace import tracer
    from ddtrace.contrib.structlog import patch
    from ddtrace.contrib.structlog import unpatch

    config.service = "logging"
    config.env = "global.env"
    config.version = "global.version"

    patch()

    cf = structlog.testing.CapturingLoggerFactory()
    structlog.configure(
        processors=(structlog.stdlib.add_log_level, structlog.processors.JSONRenderer()),
        logger_factory=cf,
    )
    logger = structlog.getLogger()

    span = tracer.trace("test.logging")
    logger.info("Hello!")
    span.finish()

    output = cf.logger.calls

    assert json.loads(output[0].args[0])["event"] == "Hello!"
    assert json.loads(output[0].args[0])["dd.trace_id"] == "{:032x}".format(span.trace_id)
    assert json.loads(output[0].args[0])["dd.span_id"] == str(span.span_id)
    assert json.loads(output[0].args[0])["dd.env"] == "global.env"
    assert json.loads(output[0].args[0])["dd.service"] == "logging"
    assert json.loads(output[0].args[0])["dd.version"] == "global.version"

    cf.logger.calls.clear()
    unpatch()


@pytest.mark.subprocess()
def test_no_configured_processor():
    """
    Check if injected values are present when no processor is configured
    """
    import structlog

    from ddtrace import config
    from ddtrace import tracer
    from ddtrace.contrib.structlog import patch
    from ddtrace.contrib.structlog import unpatch

    config.service = "logging"
    config.env = "global.env"
    config.version = "global.version"

    patch()

    cf = structlog.testing.CapturingLoggerFactory()
    structlog.configure(
        logger_factory=cf,
    )
    logger = structlog.getLogger()

    span = tracer.trace("test.logging")
    logger.info("Hello!")
    span.finish()

    output = cf.logger.calls

    assert "Hello!" in output[0].args[0]
    assert "dd.trace_id={}".format("{:032x}".format(span.trace_id)) in output[0].args[0]
    assert "dd.span_id={}".format(str(span.span_id)) in output[0].args[0]
    assert "dd.env=global.env" in output[0].args[0]
    assert "dd.service=logging" in output[0].args[0]
    assert "dd.version=global.version" in output[0].args[0]

    cf.logger.calls.clear()
    unpatch()


@pytest.mark.subprocess()
def test_two_loggers_no_duplicates():
    """
    Check if patching two loggers does not duplicate processors
    """
    import structlog

    from ddtrace.contrib.structlog import patch
    from ddtrace.contrib.structlog import unpatch

    patch()

    logger1 = structlog.getLogger()
    num_log1_processors = len(logger1.bind()._processors)

    logger2 = structlog.getLogger()
    num_log2_processors = len(logger2.bind()._processors)

    assert (
        num_log1_processors
        == num_log2_processors
        == len(logger1.bind()._processors)
        == len(structlog._config._BUILTIN_DEFAULT_PROCESSORS) + 1
    )

    unpatch()


@pytest.mark.subprocess()
def test_configure_processor():
    """
    Check if calling configure() after get_logger() does not duplicate/remove injected processors
    """
    import structlog

    from ddtrace.contrib.structlog import patch
    from ddtrace.contrib.structlog import unpatch

    patch()

    structlog.configure()
    logger1 = structlog.getLogger()
    num_log1_processors = len(logger1.bind()._processors)

    cf = structlog.testing.CapturingLoggerFactory()
    structlog.configure(
        logger_factory=cf,
    )

    assert (
        num_log1_processors == len(logger1.bind()._processors) == len(structlog._config._BUILTIN_DEFAULT_PROCESSORS) + 1
    )

    cf.logger.calls.clear()
    unpatch()


@pytest.mark.subprocess()
def test_consistent_empty_config():
    """
    Check if calling configure() with an empty processor list stays empty
    """
    import structlog

    from ddtrace.contrib.structlog import patch
    from ddtrace.contrib.structlog import unpatch

    patch()

    logger1 = structlog.getLogger()
    structlog.configure(processors=[])
    num_log1_processors = len(logger1.bind()._processors)

    logger1 = structlog.getLogger()

    assert num_log1_processors == len(logger1.bind()._processors) == 0

    unpatch()


@pytest.mark.subprocess()
def test_reset_defaults():
    """
    Check if injected processor still exists after resetting to default
    """
    import structlog

    from ddtrace.contrib.structlog import patch
    from ddtrace.contrib.structlog import unpatch

    patch()

    structlog.configure()
    logger1 = structlog.getLogger()
    num_log1_processors = len(logger1.bind()._processors)

    structlog.reset_defaults()

    assert (
        num_log1_processors == len(logger1.bind()._processors) == len(structlog._config._BUILTIN_DEFAULT_PROCESSORS) + 1
    )

    unpatch()
