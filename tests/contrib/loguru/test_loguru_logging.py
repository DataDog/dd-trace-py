import json

from loguru import logger
import pytest

from ddtrace import config
from ddtrace import tracer
from ddtrace.constants import ENV_KEY
from ddtrace.constants import SERVICE_KEY
from ddtrace.constants import VERSION_KEY
from ddtrace.contrib.loguru import patch
from ddtrace.contrib.loguru import unpatch
from tests.utils import override_global_config


captured_logs = []


def _test_logging(output, span, env, service, version):
    dd_trace_id, dd_span_id = (span.trace_id, span.span_id) if span else (0, 0)

    if dd_trace_id != 0 and not config._128_bit_trace_id_logging_enabled:
        dd_trace_id = span._trace_id_64bits

    assert "Hello" in json.loads(output[0])["text"]
    assert json.loads(output[0])["record"]["extra"]["dd.trace_id"] == str(dd_trace_id)
    assert json.loads(output[0])["record"]["extra"]["dd.span_id"] == str(dd_span_id)
    assert json.loads(output[0])["record"]["extra"]["dd.env"] == env or ""
    assert json.loads(output[0])["record"]["extra"]["dd.service"] == service or ""
    assert json.loads(output[0])["record"]["extra"]["dd.version"] == version or ""


@pytest.fixture(autouse=True)
def patch_loguru():
    try:
        patch()
        logger.add(captured_logs.append, serialize=True)
        yield
    finally:
        unpatch()


@pytest.fixture(autouse=True)
def global_config():
    with override_global_config({"service": "logging", "env": "global.env", "version": "global.version"}):
        yield
    captured_logs.clear()


def test_log_trace_global_values():
    """
    Check trace info includes global values over local span values
    """
    span = tracer.trace("test.logging")
    span.set_tag(ENV_KEY, "local-env")
    span.set_tag(SERVICE_KEY, "local-service")
    span.set_tag(VERSION_KEY, "local-version")

    logger.info("Hello!")
    span.finish()
    _test_logging(captured_logs, span, config.env, config.service, config.version)


def test_log_no_trace():
    logger.info("Hello!")

    _test_logging(captured_logs, None, config.env, config.service, config.version)


@pytest.mark.subprocess(env=dict(DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="False"))
def test_log_trace():
    """
    Check logging patched and formatter including trace info when 64bit trace ids are generated.
    """

    import json

    from loguru import logger

    from ddtrace import config
    from ddtrace import tracer
    from ddtrace.contrib.loguru import patch
    from ddtrace.contrib.loguru import unpatch

    config.service = "logging"
    config.env = "global.env"
    config.version = "global.version"

    patch()

    captured_logs = []
    logger.remove()
    logger.add(captured_logs.append, serialize=True)

    span = tracer.trace("test.logging")
    logger.debug("Hello!")
    span.finish()

    assert "Hello" in json.loads(captured_logs[0])["text"]
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.trace_id"] == str(span.trace_id)
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.span_id"] == str(span.span_id)
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.env"] == "global.env"
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.service"] == "logging"
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.version"] == "global.version"

    captured_logs.clear()
    unpatch()


@pytest.mark.subprocess(
    env=dict(DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="True", DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED="True")
)
def test_log_trace_128bit_trace_ids():
    """
    Check if 128bit trace ids are logged when `DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED=True`
    """

    import json

    from loguru import logger

    from ddtrace import config
    from ddtrace import tracer
    from ddtrace.contrib.loguru import patch
    from ddtrace.contrib.loguru import unpatch
    from ddtrace.internal.constants import MAX_UINT_64BITS

    config.service = "logging"
    config.env = "global.env"
    config.version = "global.version"

    patch()

    captured_logs = []
    logger.remove()
    logger.add(captured_logs.append, serialize=True)

    span = tracer.trace("test.logging")
    logger.debug("Hello!")
    span.finish()

    assert span.trace_id > MAX_UINT_64BITS
    assert "Hello" in json.loads(captured_logs[0])["text"]
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.trace_id"] == str(span.trace_id)
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.span_id"] == str(span.span_id)
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.env"] == "global.env"
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.service"] == "logging"
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.version"] == "global.version"

    captured_logs.clear()
    unpatch()


@pytest.mark.subprocess(
    env=dict(DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="True", DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED="False")
)
def test_log_trace_128bit_trace_ids_log_64bits():
    """
    Check if a 64 bit trace, trace id is logged when `DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED=False`
    """

    import json

    from loguru import logger

    from ddtrace import config
    from ddtrace import tracer
    from ddtrace.contrib.loguru import patch
    from ddtrace.contrib.loguru import unpatch
    from ddtrace.internal.constants import MAX_UINT_64BITS

    config.service = "logging"
    config.env = "global.env"
    config.version = "global.version"

    patch()

    captured_logs = []
    logger.remove()
    logger.add(captured_logs.append, serialize=True)

    span = tracer.trace("test.logging")
    logger.debug("Hello!")
    span.finish()

    assert span.trace_id > MAX_UINT_64BITS
    assert "Hello" in json.loads(captured_logs[0])["text"]
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.trace_id"] == str(span._trace_id_64bits)
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.span_id"] == str(span.span_id)
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.env"] == "global.env"
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.service"] == "logging"
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.version"] == "global.version"

    captured_logs.clear()
    unpatch()


@pytest.mark.subprocess(env=dict(DD_TAGS="service:ddtagservice,env:ddenv,version:ddversion"))
def test_log_DD_TAGS():
    import json

    from loguru import logger

    from ddtrace import tracer
    from ddtrace.contrib.loguru import patch
    from ddtrace.contrib.loguru import unpatch

    patch()

    captured_logs = []
    logger.remove()
    logger.add(captured_logs.append, serialize=True)

    span = tracer.trace("test.logging")
    logger.debug("Hello!")
    span.finish()

    assert "Hello" in json.loads(captured_logs[0])["text"]
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.trace_id"] == str(span._trace_id_64bits)
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.span_id"] == str(span.span_id)
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.env"] == "ddenv"
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.service"] == "ddtagservice"
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.version"] == "ddversion"

    captured_logs.clear()
    unpatch()


@pytest.mark.subprocess
def test_configured_format():
    """
    Ensure injection works when user configured format is used
    """

    def log_format(record):
        record["extra"]["serialized"] = serialize(record)
        return "{extra[serialized]}\n"

    def serialize(record):
        subset = {
            "text": record["message"],
            "dd.trace_id": record["dd.trace_id"],
            "dd.span_id": record["dd.span_id"],
            "dd.env": record["dd.env"],
            "dd.version": record["dd.version"],
            "dd.service": record["dd.service"],
        }

        return json.dumps(subset)

    import json

    from loguru import logger

    from ddtrace import config
    from ddtrace import tracer
    from ddtrace.contrib.loguru import patch
    from ddtrace.contrib.loguru import unpatch

    config.service = "logging"
    config.env = "global.env"
    config.version = "global.version"

    patch()

    captured_logs = []
    logger.remove()
    logger.add(captured_logs.append, format=log_format)

    span = tracer.trace("test.logging")
    logger.debug("Hello!")
    span.finish()

    assert "Hello" in json.loads(captured_logs[0])["text"]
    assert json.loads(captured_logs[0])["dd.trace_id"] == str(span._trace_id_64bits)
    assert json.loads(captured_logs[0])["dd.span_id"] == str(span.span_id)
    assert json.loads(captured_logs[0])["dd.env"] == "global.env"
    assert json.loads(captured_logs[0])["dd.service"] == "logging"
    assert json.loads(captured_logs[0])["dd.version"] == "global.version"

    captured_logs.clear()
    unpatch()
