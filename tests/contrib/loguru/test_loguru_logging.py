import json
import os

from loguru import logger
import pytest

from ddtrace import config
from ddtrace.constants import ENV_KEY
from ddtrace.constants import SERVICE_KEY
from ddtrace.constants import VERSION_KEY
from ddtrace.contrib.internal.loguru.patch import patch
from ddtrace.contrib.internal.loguru.patch import unpatch
from ddtrace.internal.constants import MAX_UINT_64BITS
from ddtrace.trace import tracer
from tests.utils import override_global_config


def _test_logging(output, span, env, service, version):
    dd_trace_id, dd_span_id = (span.trace_id, span.span_id) if span else (0, 0)

    if dd_trace_id > MAX_UINT_64BITS:
        dd_trace_id = "{:032x}".format(dd_trace_id)

    assert "Hello" in json.loads(output[0])["text"]
    assert json.loads(output[0])["record"]["extra"]["dd.trace_id"] == str(dd_trace_id)
    assert json.loads(output[0])["record"]["extra"]["dd.span_id"] == str(dd_span_id)
    assert json.loads(output[0])["record"]["extra"]["dd.env"] == env or ""
    assert json.loads(output[0])["record"]["extra"]["dd.service"] == service or ""
    assert json.loads(output[0])["record"]["extra"]["dd.version"] == version or ""


@pytest.fixture()
def captured_logs():
    captured_logs = []
    try:
        patch()
        logger.add(captured_logs.append, serialize=True)
        yield captured_logs
    finally:
        unpatch()


@pytest.fixture(autouse=True)
def global_config():
    with override_global_config({"service": "logging", "env": "global.env", "version": "global.version"}):
        yield


def test_log_trace_global_values(captured_logs):
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


def test_log_no_trace(captured_logs):
    logger.info("Hello!")

    _test_logging(captured_logs, None, config.env, config.service, config.version)


def test_log_with_default_sink(ddtrace_run_python_code_in_subprocess):
    code = """
from loguru import logger
from ddtrace.trace import tracer

with tracer.trace("test.logging") as span:
    logger.info("Hello!")
    """

    env = os.environ.copy()
    env.update(dict(LOGURU_SERIALIZE="1", DD_LOGS_INJECTION="1", DD_SERVICE="dds", DD_ENV="ddenv", DD_VERSION="vv"))
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)

    assert status == 0, err + out

    # default sink is stderr
    result = json.loads(err)
    assert "Hello!" in result["text"]
    assert result["record"]["extra"]["dd.trace_id"] != ""
    assert result["record"]["extra"]["dd.span_id"] != ""
    assert result["record"]["extra"]["dd.env"] == "ddenv"
    assert result["record"]["extra"]["dd.service"] == "dds"
    assert result["record"]["extra"]["dd.version"] == "vv"


def test_log_with_default_sink_and_configure(ddtrace_run_python_code_in_subprocess):
    code = """
from loguru import logger
from ddtrace.trace import tracer

logger.configure(patcher=lambda r: r.update({"extra": {"dd.new": "cc"}}))

with tracer.trace("test.logging") as span:
    logger.info("Hello!")
    """

    env = os.environ.copy()
    env.update(dict(LOGURU_SERIALIZE="1", DD_LOGS_INJECTION="1", DD_SERVICE="dds", DD_ENV="ddenv", DD_VERSION="vv"))
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)

    assert status == 0, err + out

    # default sink is stderr
    result = json.loads(err)
    assert "Hello!" in result["text"]
    assert result["record"]["extra"]["dd.trace_id"] != ""
    assert result["record"]["extra"]["dd.span_id"] != ""
    assert result["record"]["extra"]["dd.env"] == "ddenv"
    assert result["record"]["extra"]["dd.service"] == "dds"
    assert result["record"]["extra"]["dd.version"] == "vv"
    assert result["record"]["extra"]["dd.new"] == "cc"


@pytest.mark.subprocess(
    ddtrace_run=True, env=dict(DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="False", DD_LOGS_INJECTION="1")
)
def test_log_trace():
    """
    Check logging patched and formatter including trace info when 64bit trace ids are generated.
    """

    import json

    from loguru import logger

    from ddtrace import config
    from ddtrace.trace import tracer

    config.service = "logging"
    config.env = "global.env"
    config.version = "global.version"

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


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="True",
        DD_LOGS_INJECTION="1",
    ),
)
def test_log_trace_128bit_trace_ids():
    """
    Check if 128bit trace ids are logged in hex
    """

    import json

    from loguru import logger

    from ddtrace import config
    from ddtrace.internal.constants import MAX_UINT_64BITS
    from ddtrace.trace import tracer

    config.service = "logging"
    config.env = "global.env"
    config.version = "global.version"

    captured_logs = []
    logger.remove()
    logger.add(captured_logs.append, serialize=True)

    span = tracer.trace("test.logging")
    logger.debug("Hello!")
    span.finish()

    assert span.trace_id > MAX_UINT_64BITS
    assert "Hello" in json.loads(captured_logs[0])["text"]
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.trace_id"] == "{:032x}".format(span.trace_id)
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.span_id"] == str(span.span_id)
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.env"] == "global.env"
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.service"] == "logging"
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.version"] == "global.version"


@pytest.mark.subprocess(
    ddtrace_run=True, env=dict(DD_TAGS="service:ddtagservice,env:ddenv,version:ddversion", DD_LOGS_INJECTION="1")
)
def test_log_DD_TAGS():
    import json

    from loguru import logger

    from ddtrace.internal.constants import MAX_UINT_64BITS
    from ddtrace.trace import tracer

    captured_logs = []
    logger.remove()
    logger.add(captured_logs.append, serialize=True)

    span = tracer.trace("test.logging")
    logger.debug("Hello!")
    span.finish()

    trace_id = span.trace_id
    if span.trace_id > MAX_UINT_64BITS:
        trace_id = "{:032x}".format(span.trace_id)

    assert "Hello" in json.loads(captured_logs[0])["text"]
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.trace_id"] == str(trace_id)
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.span_id"] == str(span.span_id)
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.env"] == "ddenv"
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.service"] == "ddtagservice"
    assert json.loads(captured_logs[0])["record"]["extra"]["dd.version"] == "ddversion"


@pytest.mark.subprocess(ddtrace_run=True, env=dict(DD_LOGS_INJECTION="1"))
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
    from ddtrace.internal.constants import MAX_UINT_64BITS
    from ddtrace.trace import tracer

    config.service = "logging"
    config.env = "global.env"
    config.version = "global.version"

    captured_logs = []
    logger.remove()
    logger.add(captured_logs.append, format=log_format)

    span = tracer.trace("test.logging")
    logger.debug("Hello!")
    span.finish()

    trace_id = span.trace_id
    if span.trace_id > MAX_UINT_64BITS:
        trace_id = "{:032x}".format(span.trace_id)

    assert "Hello" in json.loads(captured_logs[0])["text"]
    assert json.loads(captured_logs[0])["dd.trace_id"] == str(trace_id)
    assert json.loads(captured_logs[0])["dd.span_id"] == str(span.span_id)
    assert json.loads(captured_logs[0])["dd.env"] == "global.env"
    assert json.loads(captured_logs[0])["dd.service"] == "logging"
    assert json.loads(captured_logs[0])["dd.version"] == "global.version"
