import os

import pytest

from .test_integration import AGENT_VERSION


def _get_latest_telemetry_config_item(events, item_name):
    for event in reversed(sorted(events, key=lambda e: (e["tracer_time"], e["seq_id"]))):
        for item in reversed(event.get("payload", {}).get("configuration", [])):
            if item_name == item["name"]:
                return item
    return None


@pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
def test_setting_origin_environment(test_agent_session, run_python_code_in_subprocess):
    env = os.environ.copy()
    env.update(
        {
            "DD_TRACE_SAMPLE_RATE": "0.1",
            "DD_LOGS_INJECTION": "true",
            "DD_TRACE_HEADER_TAGS": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2",
            "DD_TAGS": "team:apm,component:web",
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

    events = test_agent_session.get_events()
    assert _get_latest_telemetry_config_item(events, "trace_sample_rate") == {
        "name": "trace_sample_rate",
        "value": "0.1",
        "origin": "env_var",
    }
    assert _get_latest_telemetry_config_item(events, "logs_injection_enabled") == {
        "name": "logs_injection_enabled",
        "value": "true",
        "origin": "env_var",
    }
    assert _get_latest_telemetry_config_item(events, "trace_header_tags") == {
        "name": "trace_header_tags",
        "value": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2",
        "origin": "env_var",
    }
    assert _get_latest_telemetry_config_item(events, "trace_tags") == {
        "name": "trace_tags",
        "value": "team:apm,component:web",
        "origin": "env_var",
    }


@pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
def test_setting_origin_code(test_agent_session, run_python_code_in_subprocess):
    env = os.environ.copy()
    env.update(
        {
            "DD_TRACE_SAMPLE_RATE": "0.1",
            "DD_LOGS_INJECTION": "true",
            "DD_TRACE_HEADER_TAGS": "X-Header-Tag-1:header_tag_1,X-Header-Tag-2:header_tag_2",
            "DD_TAGS": "team:apm,component:web",
        }
    )
    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace import config, tracer
config._trace_sample_rate = 0.2
config.logs_injection = False
config.trace_http_header_tags = {"header": "value"}
config.tags = {"header": "value"}
with tracer.trace("test") as span:
    pass
        """,
        env=env,
    )
    assert status == 0, err

    events = test_agent_session.get_events()
    assert _get_latest_telemetry_config_item(events, "trace_sample_rate") == {
        "name": "trace_sample_rate",
        "value": "0.2",
        "origin": "code",
    }
    assert _get_latest_telemetry_config_item(events, "logs_injection_enabled") == {
        "name": "logs_injection_enabled",
        "value": "false",
        "origin": "code",
    }
    assert _get_latest_telemetry_config_item(events, "trace_header_tags") == {
        "name": "trace_header_tags",
        "value": "header:value",
        "origin": "code",
    }
    assert _get_latest_telemetry_config_item(events, "trace_tags") == {
        "name": "trace_tags",
        "value": "header:value",
        "origin": "code",
    }


@pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
def test_remoteconfig_sampling_rate_default(test_agent_session, run_python_code_in_subprocess):
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
        """,
    )
    assert status == 0, err

    events = test_agent_session.get_events()
    assert _get_latest_telemetry_config_item(events, "trace_sample_rate") == {
        "name": "trace_sample_rate",
        "value": "1.0",
        "origin": "default",
    }


@pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
def test_remoteconfig_sampling_rate_telemetry(test_agent_session, run_python_code_in_subprocess):
    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace import config, tracer
from tests.internal.test_settings import _base_rc_config

config._handle_remoteconfig(_base_rc_config({"tracing_sampling_rate": 0.5}))
with tracer.trace("test") as span:
    pass
assert span.get_metric("_dd.rule_psr") == 0.5
        """,
    )
    assert status == 0, err

    events = test_agent_session.get_events()
    assert _get_latest_telemetry_config_item(events, "trace_sample_rate") == {
        "name": "trace_sample_rate",
        "value": "0.5",
        "origin": "remote_config",
    }
