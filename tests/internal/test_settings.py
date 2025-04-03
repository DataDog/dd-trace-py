import json
import os
from typing import Sequence

import mock
import pytest

from ddtrace._trace.product import apm_tracing_rc
from ddtrace.internal.remoteconfig import Payload
from ddtrace.settings._config import Config
from tests.utils import remote_config_build_payload as build_payload


@pytest.fixture
def config():
    import ddtrace

    original_config = ddtrace.config
    ddtrace.config = Config()
    yield ddtrace.config
    # Reset the config to its original state
    ddtrace.config = original_config


def _base_rc_config(cfg):
    return [
        build_payload(
            "APM_TRACING",
            # this flare data can often come in and we want to make sure we're pulling the
            # actual lib_config data out correctly regardless
            {
                "internal_order": [
                    "flare-log-level.trace",
                    "flare-log-level.debug",
                    "flare-log-level.info",
                    "flare-log-level.warn",
                    "flare-log-level.error",
                    "flare-log-level.critical",
                    "flare-log-level.off",
                ],
                "order": [],
            },
            "config",
        ),
        build_payload(
            "APM_TRACING",
            {
                "action": "enable",
                "service_target": {"service": None, "env": None},
                "lib_config": cfg,
            },
            "config",
            sha_hash="1234",
        ),
    ]


def _deleted_rc_config():
    return [build_payload("APM_TRACING", None, "config", sha_hash="1234")]


def call_apm_tracing_rc(payloads: Sequence[Payload]):
    for payload in payloads:
        if payload.content is None:
            continue
        if (lib_config := payload.content.get("lib_config")) is not None:
            apm_tracing_rc(lib_config)


@pytest.mark.parametrize(
    "testcase",
    [
        {
            "expected": {
                "_trace_sampling_rules": "",
                "_logs_injection": False,
                "_trace_http_header_tags": {},
            },
            "expected_source": {
                "_trace_sampling_rules": "default",
                "_logs_injection": "default",
                "_trace_http_header_tags": "default",
            },
        },
        {
            "env": {"DD_TRACE_SAMPLING_RULES": '[{"sample_rate":0.91}]'},
            "expected": {"_trace_sampling_rules": '[{"sample_rate":0.91}]'},
            "expected_source": {"_trace_sampling_rules": "env_var"},
        },
        {
            "env": {"DD_TRACE_SAMPLING_RULES": '[{"sample_rate":0.92}]'},
            "code": {"_trace_sampling_rules": '[{"sample_rate":0.82}]'},
            "expected": {"_trace_sampling_rules": '[{"sample_rate":0.82}]'},
            "expected_source": {"_trace_sampling_rules": "code"},
        },
        {
            "env": {"DD_TRACE_SAMPLING_RULES": '[{"sample_rate":0.93}]'},
            "code": {"_trace_sampling_rules": '[{"sample_rate":0.83}]'},
            "rc": {
                "tracing_sampling_rules": [
                    {
                        "sample_rate": "0.73",
                        "service": "*",
                        "name": "*",
                        "resource": "*",
                        "tags": [],
                        "provenance": "customer",
                    }
                ]
            },
            "expected": {
                "_trace_sampling_rules": '[{"sample_rate": "0.73", "service": "*", "name": "*", '
                '"resource": "*", "tags": [], "provenance": "customer"}]',
            },
            "expected_source": {"_trace_sampling_rules": "remote_config"},
        },
        {
            "env": {"DD_LOGS_INJECTION": "true"},
            "expected": {"_logs_injection": True},
            "expected_source": {"_logs_injection": "env_var"},
        },
        {
            "env": {"DD_LOGS_INJECTION": "true"},
            "code": {"_logs_injection": False},
            "expected": {"_logs_injection": False},
            "expected_source": {"_logs_injection": "code"},
        },
        {
            "env": {"DD_TRACE_HEADER_TAGS": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2"},
            "expected": {
                "_trace_http_header_tags": {"X-Header-Tag-1": "header_tag_1", "X-Header-Tag-2": "header_tag_2"}
            },
            "expected_source": {"_trace_http_header_tags": "env_var"},
        },
        {
            "env": {"DD_TRACE_HEADER_TAGS": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2"},
            "code": {"_trace_http_header_tags": {"header": "value"}},
            "expected": {"_trace_http_header_tags": {"header": "value"}},
            "expected_source": {"_trace_http_header_tags": "code"},
        },
        {
            "env": {"DD_TRACE_HEADER_TAGS": "X-Header-Tag-1,X-Header-Tag-2,X-Header-Tag-3:specific_tag3"},
            "expected": {
                "_trace_http_header_tags": {
                    "X-Header-Tag-1": "",
                    "X-Header-Tag-2": "",
                    "X-Header-Tag-3": "specific_tag3",
                }
            },
            "expected_source": {"_trace_http_header_tags": "env_var"},
        },
        {
            "env": {"DD_TRACE_HEADER_TAGS": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2"},
            "rc": {
                "tracing_header_tags": [
                    {"header": "X-Header-Tag-69", "tag_name": "header_tag_69"},
                    {"header": "X-Header-Tag-70", "tag_name": ""},
                ]
            },
            "code": {"_trace_http_header_tags": {"header": "value"}},
            "expected": {
                "_trace_http_header_tags": {
                    "X-Header-Tag-69": "header_tag_69",
                    "X-Header-Tag-70": "",
                }
            },
            "expected_source": {"_trace_http_header_tags": "remote_config"},
        },
        {
            "env": {"DD_TAGS": "key:value,key2:value2"},
            "expected": {"tags": {"key": "value", "key2": "value2"}},
            "expected_source": {"tags": "env_var"},
        },
        {
            "env": {"DD_TAGS": "key:value,key2:value2"},
            "code": {"tags": {"k": "v", "k2": "v2"}},
            "expected": {"tags": {"k": "v", "k2": "v2"}},
            "expected_source": {"tags": "code"},
        },
        {
            "env": {"DD_TAGS": "key:value,key2:value2"},
            "code": {"tags": {"k": "v", "k2": "v2"}},
            "rc": {"tracing_tags": ["key1:val2", "key2:val3"]},
            "expected": {"tags": {"key1": "val2", "key2": "val3"}},
            "expected_source": {"tags": "remote_config"},
        },
        {
            "env": {"DD_TRACE_ENABLED": "true"},
            "code": {"_tracing_enabled": True},
            "rc": {"tracing_enabled": "true"},
            "expected": {"_tracing_enabled": True},
            "expected_source": {"_tracing_enabled": "remote_config"},
        },
        {
            "env": {"DD_TRACE_ENABLED": "true"},
            "code": {"_tracing_enabled": True},
            "rc": {"tracing_enabled": "false"},
            "expected": {"_tracing_enabled": False},
            "expected_source": {"_tracing_enabled": "remote_config"},
        },
        {
            "env": {"DD_TRACE_ENABLED": "false"},
            "expected": {"_tracing_enabled": False},
            "expected_source": {"_tracing_enabled": "env_var"},
        },
    ],
)
def test_settings_parametrized(testcase, config, monkeypatch):
    for env_name, env_value in testcase.get("env", {}).items():
        monkeypatch.setenv(env_name, env_value)
        config._reset()

    for code_name, code_value in testcase.get("code", {}).items():
        setattr(config, code_name, code_value)

    rc_items = testcase.get("rc", {})
    if rc_items:
        call_apm_tracing_rc(_base_rc_config(rc_items))

    for expected_name, expected_value in testcase["expected"].items():
        assert getattr(config, expected_name) == expected_value

    for expected_name, expected_source in testcase.get("expected_source", {}).items():
        assert config._get_source(expected_name) == expected_source


def test_settings_missing_lib_config(config, monkeypatch):
    testcase = {
        "env": {"DD_TRACE_ENABLED": "true"},
        "code": {"_tracing_enabled": True},
        "rc": {},
        "expected": {"_tracing_enabled": True},
        "expected_source": {"_tracing_enabled": "code"},
    }
    for env_name, env_value in testcase.get("env", {}).items():
        monkeypatch.setenv(env_name, env_value)
        config._reset()

    for code_name, code_value in testcase.get("code", {}).items():
        setattr(config, code_name, code_value)

    base_rc_config = _base_rc_config({})

    # Delete "lib_config" from the remote config
    del base_rc_config[1].content["lib_config"]
    assert "lib_config" not in base_rc_config[0].content

    call_apm_tracing_rc(base_rc_config)

    for expected_name, expected_value in testcase["expected"].items():
        assert getattr(config, expected_name) == expected_value

    for expected_name, expected_source in testcase.get("expected_source", {}).items():
        assert config._get_source(expected_name) == expected_source


def test_config_subscription(config):
    for s in ("_trace_sampling_rules", "_logs_injection", "_trace_http_header_tags"):
        _handler = mock.MagicMock()
        config._subscribe([s], _handler)
        setattr(config, s, "1")
        _handler.assert_called_once_with(config, [s])


def test_remoteconfig_sampling_rules(run_python_code_in_subprocess):
    env = os.environ.copy()
    env.update({"DD_TRACE_SAMPLING_RULES": '[{"sample_rate":0.1, "name":"test"}]'})

    _, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace import config, tracer
from ddtrace._trace.sampler import DatadogSampler
from tests.internal.test_settings import _base_rc_config, _deleted_rc_config, call_apm_tracing_rc

# Span is sampled using sampling rules from env var
with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.1
assert span.get_tag("_dd.p.dm") == "-3"

call_apm_tracing_rc(_base_rc_config({"tracing_sampling_rules":[
        {
            "service": "*",
            "name": "test",
            "resource": "*",
            "provenance": "customer",
            "sample_rate": 0.2,
        }
        ]}))
# Span is sampled using sampling rules from the agent
with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.2
assert span.get_tag("_dd.p.dm") == "-11"
# Span is sampling using default sampling rate
with tracer.trace("does_not_match_remote_rules") as span:
    pass
assert span.get_tag("_dd.p.dm") == "-0"
assert span.context.sampling_priority == 1

# Agent sampling rules do not contain any sampling rules
call_apm_tracing_rc(_base_rc_config({}))
# Span is sampled using sampling rules from env var
with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.1

# Agent sampling rules are set to match service, name, and resource
call_apm_tracing_rc(_base_rc_config({"tracing_sampling_rules":[
        {
            "service": "ok",
            "name": "test",
            "resource": "hello",
            "provenance": "customer",
            "sample_rate": 0.4,
        }
        ]}))
# Span is sampled using sampling rules from the agent
with tracer.trace("test", service="ok", resource="hello") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.4
assert span.get_tag("_dd.p.dm") == "-11"
# Span is sampling using default sampling rate (does not match new rules)
with tracer.trace("test") as span:
    pass
assert span.get_tag("_dd.p.dm") == "-0"
assert span.context.sampling_priority == 1
        """,
        env=env,
    )
    assert status == 0, err.decode("utf-8")


def test_remoteconfig_global_sample_rate_and_rules(run_python_code_in_subprocess):
    """There is complex logic regarding the interaction between setting new
    sample rates and rules with remote config.
    """
    env = os.environ.copy()
    env.update({"DD_TRACE_SAMPLING_RULES": '[{"sample_rate":0.9, "name":"rules"}, {"sample_rate":0.8}]'})

    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace import config, tracer
from ddtrace._trace.sampler import DatadogSampler
from tests.internal.test_settings import _base_rc_config, _deleted_rc_config, call_apm_tracing_rc

# Ensure that sampling rule with operation name "rules" is used
with tracer.trace("rules") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.9
assert span.get_tag("_dd.p.dm") == "-3"
# Ensure that the global sampling rule is used
with tracer.trace("sample_rate") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.8
assert span.get_tag("_dd.p.dm") == "-3"
# Override all sampling rules set via env var with a new rule
call_apm_tracing_rc(_base_rc_config({"tracing_sampling_rules":[
        {
            "service": "*",
            "name": "rules",
            "resource": "*",
            "provenance": "customer",
            "sample_rate": 0.7,
        }
        ]}))
# Ensure that the sampling rule from the remote config is used
with tracer.trace("rules") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.7
assert span.get_tag("_dd.p.dm") == "-11"
# Ensure that the default sampling rate of 0.9 is overwritten by rc
with tracer.trace("sample_rate") as span:
    pass
assert span.get_metric("_dd.rule_psr") is None
assert span.get_tag("_dd.p.dm") == "-0"

# Set a new default sampling rate via rc
call_apm_tracing_rc(_base_rc_config({"tracing_sampling_rate": 0.2}))
# Ensure that the new default sampling rate is used
with tracer.trace("sample_rate") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.2
assert span.get_tag("_dd.p.dm") == "-3"
# Ensure the previous sampling rule with operation name "rules" is not used
with tracer.trace("rules") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.2
assert span.get_tag("_dd.p.dm") == "-3"

# Set a new sampling rules via rc
call_apm_tracing_rc(_base_rc_config({"tracing_sampling_rate": 0.3}))
# Ensure that the new default sampling rate is used
with tracer.trace("sample_rate") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.3
assert span.get_tag("_dd.p.dm") == "-3"
# Ensure the previous sampling rule with operation name "rules" is not used
with tracer.trace("rules") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.3
assert span.get_tag("_dd.p.dm") == "-3"

# Remove all sampling rules from remote config
call_apm_tracing_rc(_base_rc_config({}))
# Ensure that the default sampling rate set via env vars is used
with tracer.trace("rules") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.9
assert span.get_tag("_dd.p.dm") == "-3"
# Ensure that the sampling rule matching operation names set via env vars is used
with tracer.trace("sample_rate") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.8
assert span.get_tag("_dd.p.dm") == "-3"

# Test swtting dynamic and customer sampling rules
call_apm_tracing_rc(_base_rc_config({"tracing_sampling_rules":[
        {
            "service": "*",
            "name": "rules_dynamic",
            "resource": "*",
            "provenance": "dynamic",
            "sample_rate": 0.1,
        },
        {
            "service": "*",
            "name": "rules_customer",
            "resource": "*",
            "provenance": "customer",
            "sample_rate": 0.6,
        }
        ]}))
# Test sampling rule with dynamic providence
with tracer.trace("rules_dynamic") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.1
assert span.get_tag("_dd.p.dm") == "-12"
# Test sampling rule with customer providence
with tracer.trace("rules_customer") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.6
assert span.get_tag("_dd.p.dm") == "-11"
# Test span that does not match any sampling rule
with tracer.trace("sample_rate") as span:
    pass
assert span.get_metric("_dd.rule_psr") is None
assert span.get_tag("_dd.p.dm") == "-0"
         """,
        env=env,
    )
    assert status == 0, err.decode("utf-8")


def test_remoteconfig_custom_tags(run_python_code_in_subprocess):
    env = os.environ.copy()
    env.update({"DD_TAGS": "team:apm"})
    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace import config, tracer
from tests.internal.test_settings import _base_rc_config, call_apm_tracing_rc

with tracer.trace("test") as span:
    pass
assert span.get_tag("team") == "apm"

call_apm_tracing_rc(_base_rc_config({"tracing_tags": ["team:onboarding"]}))

with tracer.trace("test") as span:
    pass
assert span.get_tag("team") == "onboarding", span._meta

call_apm_tracing_rc(_base_rc_config({}))
with tracer.trace("test") as span:
    pass
assert span.get_tag("team") == "apm"
        """,
        env=env,
    )
    assert status == 0, f"err={err.decode('utf-8')} out={out.decode('utf-8')}"


def test_remoteconfig_tracing_enabled(run_python_code_in_subprocess):
    env = os.environ.copy()
    env.update({"DD_TRACE_ENABLED": "true"})
    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace import config, tracer
from tests.internal.test_settings import _base_rc_config, call_apm_tracing_rc

assert tracer.enabled is True

call_apm_tracing_rc(_base_rc_config({"tracing_enabled": "false"}))

assert tracer.enabled is False

call_apm_tracing_rc(_base_rc_config({"tracing_enabled": "true"}))

assert tracer.enabled is False
        """,
        env=env,
    )
    assert status == 0, f"err={err.decode('utf-8')} out={out.decode('utf-8')}"


def test_remoteconfig_logs_injection_jsonlogger(run_python_code_in_subprocess):
    out, err, status, _ = run_python_code_in_subprocess(
        """
import logging
from pythonjsonlogger import jsonlogger
from ddtrace import config, tracer
from tests.internal.test_settings import _base_rc_config, call_apm_tracing_rc

log = logging.getLogger()
log.level = logging.CRITICAL
logHandler = logging.StreamHandler(); logHandler.setFormatter(jsonlogger.JsonFormatter())
log.addHandler(logHandler)
# Enable logs injection
call_apm_tracing_rc(_base_rc_config({"log_injection_enabled": True}))
with tracer.trace("test") as span:
    print(span.trace_id)
    log.critical("Hello, World!")
# Disable logs injection
call_apm_tracing_rc(_base_rc_config({"log_injection_enabled": False}))
with tracer.trace("test") as span:
    print(span.trace_id)
    log.critical("Hello, World!")
"""
    )

    assert status == 0, err
    trace_id = "{:032x}".format(int(out.decode("utf-8").strip().split("\n")[0]))
    log_enabled, log_disabled = map(json.loads, err.decode("utf-8").strip().split("\n")[0:2])
    assert log_enabled["dd.trace_id"] == trace_id
    assert "dd.trace_id" not in log_disabled


def test_remoteconfig_header_tags(run_python_code_in_subprocess):
    env = os.environ.copy()
    env.update({"DD_TRACE_HEADER_TAGS": "X-Header-Tag-419:env_set_tag_name"})
    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace import config, tracer
from ddtrace.contrib import trace_utils
from tests.internal.test_settings import _base_rc_config, call_apm_tracing_rc

with tracer.trace("test") as span:
    trace_utils.set_http_meta(span,
                              config.falcon,  # randomly chosen http integration config
                              request_headers={"X-Header-Tag-420": "foobarbanana", "X-Header-Tag-419": "helloworld"})
assert span.get_tag("header_tag_420") is None
assert span.get_tag("env_set_tag_name") == "helloworld"

config._http._reset()
config._header_tag_name.invalidate()
call_apm_tracing_rc(_base_rc_config({"tracing_header_tags":
    [{"header": "X-Header-Tag-420", "tag_name":"header_tag_420"}]}))

with tracer.trace("test_rc_override") as span2:
    trace_utils.set_http_meta(span2,
                              config.falcon,  # randomly chosen http integration config
                              request_headers={"X-Header-Tag-420": "foobarbanana", "X-Header-Tag-419": "helloworld"})
assert span2.get_tag("header_tag_420") == "foobarbanana", span2._meta
assert span2.get_tag("env_set_tag_name") is None

config._http._reset()
config._header_tag_name.invalidate()
call_apm_tracing_rc(_base_rc_config({}))

with tracer.trace("test") as span3:
    trace_utils.set_http_meta(span3,
                              config.falcon,  # randomly chosen http integration config
                              request_headers={"X-Header-Tag-420": "foobarbanana", "X-Header-Tag-419": "helloworld"})
assert span3.get_tag("header_tag_420") is None
assert span3.get_tag("env_set_tag_name") == "helloworld"
        """,
        env=env,
    )
    assert status == 0, f"err={err.decode('utf-8')} out={out.decode('utf-8')}"


def test_config_public_properties_and_methods():
    # Regression test to prevent unexpected changes to public attributes in Config
    # By default most attributes should be private and set via Environment Variables
    from ddtrace.settings._config import Config

    public_attrs = set()
    c = Config()
    # Check for public attributes in Config
    for attr in dir(c):
        if not attr.startswith("_") and not attr.startswith("__"):
            public_attrs.add(attr)
    # Check for public keys in Config._config
    for key in c._config:
        if not key.startswith("_"):
            public_attrs.add(key)

    assert public_attrs == {
        "service",
        "service_mapping",
        "env",
        "tags",
        "version",
    }, public_attrs
