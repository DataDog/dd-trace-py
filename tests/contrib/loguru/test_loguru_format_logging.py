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


def log_format(record):
    record["extra"]["serialized"] = serialize(record)
    return "{extra[serialized]}\n"


def _test_logging(output, span, env, service, version):
    dd_trace_id, dd_span_id = (span.trace_id, span.span_id) if span else (0, 0)

    assert "Hello" in json.loads(output[0])["text"]
    assert json.loads(output[0])["dd.trace_id"] == str(dd_trace_id)
    assert json.loads(output[0])["dd.span_id"] == str(dd_span_id)
    assert json.loads(output[0])["dd.env"] == env or ""
    assert json.loads(output[0])["dd.service"] == service or ""
    assert json.loads(output[0])["dd.version"] == version or ""

    output.clear()


@pytest.fixture(autouse=True)
def patch_loguru():
    try:
        patch()
        logger.add(captured_logs.append, format=log_format)
        yield
    finally:
        unpatch()


@pytest.fixture(autouse=True)
def global_config():
    with override_global_config({"service": "logging", "env": "global.env", "version": "global.version"}):
        yield


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
