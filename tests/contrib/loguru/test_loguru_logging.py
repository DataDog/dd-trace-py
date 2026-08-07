import json
import os

from loguru import logger
import pytest

from ddtrace import config
from ddtrace.constants import ENV_KEY
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
    assert json.loads(output[0])["record"]["extra"]["dd.env"] in (env or "")
    assert json.loads(output[0])["record"]["extra"]["dd.service"] in (service or "")
    assert json.loads(output[0])["record"]["extra"]["dd.version"] in (version or "")


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


@pytest.mark.subprocess(
    ddtrace_run=True,
    parametrize=dict(DD_LOGS_INJECTION=["true", None]),
    env=dict(DD_SERVICE="dds", DD_ENV="ddenv", DD_VERSION="vv"),
    out=None,
    err=None,
)
def test_log_injection_enabled():
    from loguru import logger

    from ddtrace import tracer
    from tests.contrib.loguru.test_loguru_logging import _test_logging

    captured_logs = []
    logger.add(captured_logs.append, serialize=True)
    with tracer.trace("test.logging") as span:
        logger.info("Hello!")

    _test_logging(captured_logs, span, "ddenv", "dds", "vv")


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_SERVICE="dds", DD_ENV="ddenv", DD_VERSION="vv", DD_LOGS_INJECTION="false"),
    out=None,
    err=None,
)
def test_log_injection_disabled():
    import json

    from loguru import logger

    from ddtrace import tracer

    captured_logs = []
    logger.add(captured_logs.append, serialize=True)
    with tracer.trace("test.logging"):
        logger.info("Hello!")

    for log in captured_logs:
        log_json = json.loads(log)
        assert not log_json["record"]["extra"], "Log record should not contain ddtrace fields when"
        f"DD_LOGS_INJECTION is disabled, record: {log_json.get('record')}"


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_SERVICE="dds", DD_ENV="ddenv", DD_VERSION="vv", DD_LOGS_INJECTION="false"),
    out=None,
    err=None,
)
def test_log_injection_disabled_does_not_copy_extra_into_record():
    """Regression: ``_tracer_injection`` returned ``event_dict`` (=
    ``record["extra"]``) when ``DD_LOGS_INJECTION=false``, so the patcher
    did ``record.update(record["extra"])`` and copied every user-provided
    ``extra`` key into the log record's top level — invisible to the user
    until something tried to format the record with a key that suddenly
    existed in two places. With injection disabled the helper should now
    return an empty dict and the record's top-level keys should stay
    untouched.
    """
    from loguru import logger

    from ddtrace import tracer

    # Loguru serialises records as JSON; the only way to observe the
    # full record (including dynamically-injected top-level keys) is via
    # the dict form delivered to a ``sink`` callable.
    captured_records = []

    def _record_sink(message):
        captured_records.append(dict(message.record))

    logger.add(_record_sink)

    with tracer.trace("test.logging"):
        logger.bind(user_id=42, custom_field="hello").info("hi")

    assert captured_records, "expected at least one captured record"
    for record in captured_records:
        # The user's bound extras must live in ``extra`` only — and must
        # NOT have been copied into the top-level record.
        assert record["extra"].get("user_id") == 42
        assert record["extra"].get("custom_field") == "hello"
        assert "user_id" not in (key for key in record if key != "extra"), (
            f"user_id should not have leaked to record top level: {list(record)}"
        )
        assert "custom_field" not in (key for key in record if key != "extra"), (
            f"custom_field should not have leaked to record top level: {list(record)}"
        )


def test_log_trace_global_values(captured_logs):
    span = tracer.trace("test.logging")
    span.set_tag(ENV_KEY, "local-env")
    span.service = "local-service"
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
