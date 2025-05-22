import os

import pytest

from tests.integration.utils import AGENT_VERSION


def _get_telemetry_config_items(events, item_name):
    items = []
    for event in reversed(sorted(events, key=lambda e: (e["tracer_time"], e["seq_id"]))):
        for item in reversed(event.get("payload", {}).get("configuration", [])):
            if item_name == item["name"]:
                items.append(item)
    if items:
        return items or None


@pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
def test_setting_origin_environment(test_agent_session, run_python_code_in_subprocess):
    env = os.environ.copy()
    env.update(
        {
            "DD_TRACE_SAMPLING_RULES": '[{"sample_rate":0.1}]',
            "DD_LOGS_INJECTION": "true",
            "DD_TRACE_HEADER_TAGS": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2",
            "DD_TAGS": "team:apm,component:web",
            "DD_TRACE_ENABLED": "true",
            "_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED": "true",
        }
    )
    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace import config, tracer
with tracer.trace("test") as span:
    pass
        """,
        env=env,
    )
    assert status == 0, err

    events = test_agent_session.get_events(subprocess=True)
    events_trace_sample_rate = _get_telemetry_config_items(events, "DD_TRACE_SAMPLING_RULES")

    assert {
        "name": "DD_TRACE_SAMPLING_RULES",
        "value": '[{"sample_rate":0.1}]',
        "origin": "env_var",
    } in events_trace_sample_rate

    events_logs_injection_enabled = _get_telemetry_config_items(events, "DD_LOGS_INJECTION")
    assert {"name": "DD_LOGS_INJECTION", "value": True, "origin": "env_var"} in events_logs_injection_enabled

    events_trace_header_tags = _get_telemetry_config_items(events, "DD_TRACE_HEADER_TAGS")
    assert {
        "name": "DD_TRACE_HEADER_TAGS",
        "value": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2",
        "origin": "env_var",
    } in events_trace_header_tags

    events_trace_tags = _get_telemetry_config_items(events, "DD_TAGS")
    assert {"name": "DD_TAGS", "value": "team:apm,component:web", "origin": "env_var"} in events_trace_tags

    events_tracing_enabled = _get_telemetry_config_items(events, "DD_TRACE_ENABLED")
    assert {"name": "DD_TRACE_ENABLED", "value": True, "origin": "env_var"} in events_tracing_enabled


@pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
def test_setting_origin_code(test_agent_session, run_python_code_in_subprocess):
    env = os.environ.copy()
    env.update(
        {
            "DD_LOGS_INJECTION": "true",
            "DD_TRACE_HEADER_TAGS": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2",
            "DD_TAGS": "team:apm,component:web",
            "DD_TRACE_ENABLED": "true",
            "DD_CIVISIBILITY_AGENTLESS_ENABLED": "false",
        }
    )
    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace import config, tracer

config._logs_injection = False
config._trace_http_header_tags = {"header": "value"}
config.tags = {"header": "value"}
config._tracing_enabled = False

from ddtrace.internal.telemetry import telemetry_writer
# simulate app start event, this occurs when the first span is sent to the datadog agent
telemetry_writer._app_started()
        """,
        env=env,
    )
    assert status == 0, err

    events = test_agent_session.get_events(subprocess=True)

    events_logs_injection_enabled = _get_telemetry_config_items(events, "DD_LOGS_INJECTION")
    assert {
        "name": "DD_LOGS_INJECTION",
        "value": False,
        "origin": "code",
    } in events_logs_injection_enabled

    events_trace_header_tags = _get_telemetry_config_items(events, "DD_TRACE_HEADER_TAGS")
    assert {
        "name": "DD_TRACE_HEADER_TAGS",
        "value": "header:value",
        "origin": "code",
    } in events_trace_header_tags

    events_trace_tags = _get_telemetry_config_items(events, "DD_TAGS")
    assert {
        "name": "DD_TAGS",
        "value": "header:value",
        "origin": "code",
    } in events_trace_tags

    events_tracing_enabled = _get_telemetry_config_items(events, "DD_TRACE_ENABLED")
    assert {
        "name": "DD_TRACE_ENABLED",
        "value": False,
        "origin": "code",
    } in events_tracing_enabled


@pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
def test_remoteconfig_sampling_rate_default(test_agent_session, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    env.update(
        {
            "_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED": "true",
        }
    )
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        """
from ddtrace import config, tracer
from tests.internal.test_settings import _base_rc_config
from tests.internal.test_settings import call_apm_tracing_rc

with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") is None

call_apm_tracing_rc(_base_rc_config({"tracing_sampling_rate": 0.5}), config)
with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.5

call_apm_tracing_rc(_base_rc_config({"tracing_sampling_rate": None}), config)
with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") is None, "Unsetting remote config trace sample rate"

call_apm_tracing_rc(_base_rc_config({"tracing_sampling_rate": 0.8}), config)
with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.8

call_apm_tracing_rc(_base_rc_config({"tracing_sampling_rate": None}), config)
with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") is None, "(second time) unsetting remote config trace sample rate"
        """,
        env=env,
    )
    assert status == 0, err

    events = test_agent_session.get_events(subprocess=True)
    events_trace_sample_rate = _get_telemetry_config_items(events, "DD_TRACE_SAMPLING_RULES")
    assert {"name": "DD_TRACE_SAMPLING_RULES", "value": "", "origin": "default"} in events_trace_sample_rate


@pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
def test_remoteconfig_sampling_rate_telemetry(test_agent_session, run_python_code_in_subprocess):
    env = os.environ.copy()
    env.update(
        {
            "_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED": "true",
        }
    )
    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace import config, tracer
from ddtrace._trace.product import apm_tracing_rc_subscribe
from tests.internal.test_settings import _base_rc_config
from tests.internal.test_settings import call_apm_tracing_rc

apm_tracing_rc_subscribe(config)

call_apm_tracing_rc(
    _base_rc_config(
        {
            "tracing_sampling_rules": [
                {
                    "sample_rate": "0.5",
                    "service": "*",
                    "name": "*",
                    "resource": "*",
                    "tags": [],
                    "provenance": "customer",
                }
            ]
        },
    ),
    config,
)
with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.5, span._meta
        """,
        env=env,
    )
    assert status == 0, err

    events = test_agent_session.get_events(subprocess=True)
    events_trace_sample_rate = _get_telemetry_config_items(events, "DD_TRACE_SAMPLING_RULES")
    assert {
        "name": "DD_TRACE_SAMPLING_RULES",
        "origin": "remote_config",
        "value": '[{"sample_rate": "0.5", "service": "*", "name": "*", "resource": "*", '
        '"tags": {}, "provenance": "customer"}]',
    } in events_trace_sample_rate


@pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
def test_remoteconfig_header_tags_telemetry(test_agent_session, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    env.update(
        {
            "_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED": "true",
        }
    )
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        """
from ddtrace import config, tracer
from ddtrace.contrib import trace_utils
from tests.internal.test_settings import _base_rc_config
from tests.internal.test_settings import call_apm_tracing_rc

call_apm_tracing_rc(_base_rc_config({
    "tracing_header_tags": [
        {"header": "used", "tag_name":"header_tag_69"},
        {"header": "unused", "tag_name":"header_tag_70"},
        {"header": "used-with-default", "tag_name":""}]
}), config)
with tracer.trace("test") as span:
    trace_utils.set_http_meta(
        span,
        config.falcon,  # randomly chosen http integration config
        request_headers={"used": "foobarbanana", "used-with-default": "defaultname"},
    )
assert span.get_tag("header_tag_69") == "foobarbanana"
assert span.get_tag("header_tag_70") is None
assert span.get_tag("http.request.headers.used-with-default") == "defaultname"
        """,
        env=env,
    )
    assert status == 0, err

    events = test_agent_session.get_events(subprocess=True)
    events_trace_header_tags = _get_telemetry_config_items(events, "DD_TRACE_HEADER_TAGS")
    assert {
        "name": "DD_TRACE_HEADER_TAGS",
        "value": "used:header_tag_69,unused:header_tag_70,used-with-default:",
        "origin": "remote_config",
    } in events_trace_header_tags
