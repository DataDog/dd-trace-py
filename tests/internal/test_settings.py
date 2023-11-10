import json
import mock
import pytest

from ddtrace.settings import Config


@pytest.fixture
def config():
    yield Config()


def _base_rc_config(cfg):
    lib_config = {
        "tracing_sampling_rate": None,
        "log_injection_enabled": None,
        "tracing_header_tags": None,
        "runtime_metrics_enabled": None,
        "tracing_debug": None,
        "tracing_service_mapping": None,
        "tracing_sampling_rules": None,
        "span_sampling_rules": None,
        "data_streams_enabled": None,
    }
    lib_config.update(cfg)

    return {
        "metadata": [],
        "config": [
            {
                "action": "enable",
                "service_target": {"service": None, "env": None},
                "lib_config": lib_config,
            }
        ],
    }


@pytest.mark.parametrize(
    "testcase",
    [
        {
            "expected": {
                "_trace_sample_rate": 1.0,
                "logs_injection": False,
                "trace_http_header_tags": {},
            },
            "expected_source": {
                "_trace_sample_rate": "default",
                "logs_injection": "default",
                "trace_http_header_tags": "default",
            },
        },
        {
            "env": {"DD_TRACE_SAMPLE_RATE": "0.9"},
            "expected": {"_trace_sample_rate": 0.9},
            "expected_source": {"_trace_sample_rate": "env"},
        },
        {
            "env": {"DD_TRACE_SAMPLE_RATE": "0.9"},
            "code": {"_trace_sample_rate": 0.8},
            "expected": {"_trace_sample_rate": 0.8},
            "expected_source": {"_trace_sample_rate": "code"},
        },
        {
            "env": {"DD_TRACE_SAMPLE_RATE": "0.9"},
            "code": {"_trace_sample_rate": 0.8},
            "rc": {"tracing_sampling_rate": 0.7},
            "expected": {"_trace_sample_rate": 0.7},
            "expected_source": {"_trace_sample_rate": "remote_config"},
        },
        {
            "env": {"DD_LOGS_INJECTION": "true"},
            "expected": {"logs_injection": True},
            "expected_source": {"logs_injection": "env"},
        },
        {
            "env": {"DD_LOGS_INJECTION": "true"},
            "code": {"logs_injection": False},
            "expected": {"logs_injection": False},
            "expected_source": {"logs_injection": "code"},
        },
        {
            "env": {"DD_LOGS_INJECTION": "true"},
            "code": {"logs_injection": False},
            "rc": {"log_injection_enabled": True},
            "expected": {"logs_injection": True},
            "expected_source": {"logs_injection": "remote_config"},
        },
        {
            "env": {"DD_TRACE_HEADER_TAGS": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2"},
            "expected": {
                "trace_http_header_tags": {"X-Header-Tag-1": "header_tag_1", "X-Header-Tag-2": "header_tag_2"}
            },
            "expected_source": {"trace_http_header_tags": "env"},
        },
        {
            "env": {"DD_TRACE_HEADER_TAGS": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2"},
            "code": {"trace_http_header_tags": {"header": "value"}},
            "expected": {"trace_http_header_tags": {"header": "value"}},
            "expected_source": {"trace_http_header_tags": "code"},
        },
        {
            "env": {"DD_TRACE_HEADER_TAGS": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2"},
            "code": {"trace_http_header_tags": {"header": "value"}},
            "rc": {"tracing_header_tags": [{"header": "Content-Length", "tag_name": ""}]},
            "expected": {"trace_http_header_tags": {"content-length": ""}},
            "expected_source": {"trace_http_header_tags": "remote_config"},
        },
    ],
)
def test_settings(testcase, config, monkeypatch):
    for env_name, env_value in testcase.get("env", {}).items():
        monkeypatch.setenv(env_name, env_value)
        config._reset()

    for code_name, code_value in testcase.get("code", {}).items():
        setattr(config, code_name, code_value)

    rc_items = testcase.get("rc", {}).items()
    if rc_items:
        config._handle_remoteconfig(_base_rc_config(rc_items), None)

    for expected_name, expected_value in testcase["expected"].items():
        assert getattr(config, expected_name) == expected_value

    for expected_name, expected_source in testcase.get("expected_source", {}).items():
        assert config._get_source(expected_name) == expected_source


def test_config_subscription(config):
    for s in ("_trace_sample_rate", "logs_injection", "trace_http_header_tags"):
        _handler = mock.MagicMock()
        config._subscribe([s], _handler)
        setattr(config, s, "1")
        _handler.assert_called_once_with(config, [s])


def test_remoteconfig_logs_injection_jsonlogger(run_python_code_in_subprocess):
    out, err, status, _ = run_python_code_in_subprocess(
        """
import logging
from pythonjsonlogger import jsonlogger
from ddtrace import config, tracer
from tests.internal.test_settings import _base_rc_config

log = logging.getLogger()
log.level = logging.CRITICAL
logHandler = logging.StreamHandler(); logHandler.setFormatter(jsonlogger.JsonFormatter())
log.addHandler(logHandler)

# Enable logs injection
config._handle_remoteconfig(_base_rc_config({"log_injection_enabled": True}))
with tracer.trace("test") as span:
    print(span.trace_id)
    log.critical("Hello, World!")

# Disable logs injection
config._handle_remoteconfig(_base_rc_config({"log_injection_enabled": False}))
with tracer.trace("test") as span:
    print(span.trace_id)
    log.critical("Hello, World!")
"""
    )

    assert status == 0, err
    trace_id = out.decode("utf-8").strip().split("\n")[0]
    log_enabled, log_disabled = map(json.loads, err.decode("utf-8").strip().split("\n")[0:2])
    assert log_enabled["dd.trace_id"] == trace_id
    assert "dd.trace_id" not in log_disabled


def test_remoteconfig_sampling_rate(run_python_code_in_subprocess):
    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace import config, tracer
from tests.internal.test_settings import _base_rc_config

with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") is None

config._handle_remoteconfig(_base_rc_config({"tracing_sampling_rate": 0.5}))
with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.5

config._handle_remoteconfig(_base_rc_config({"tracing_sampling_rate": None}))
with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") is None, "Unsetting remote config trace sample rate"

config._handle_remoteconfig(_base_rc_config({"tracing_sampling_rate": 0.8}))
with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.8

config._handle_remoteconfig(_base_rc_config({"tracing_sampling_rate": None}))
with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") is None, "(second time) unsetting remote config trace sample rate"
        """
    )
    assert status == 0, err
