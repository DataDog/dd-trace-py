import pytest

from ddtrace import config
from ddtrace import tracer
from ddtrace.internal.telemetry import telemetry_writer

from ..internal.test_settings import _base_rc_config
from .test_integration import AGENT_VERSION


def _get_latest_telemetry_config_item(events, item_name):
    for event in reversed(sorted(events, key=lambda e: (e["tracer_time"], e["seq_id"]))):
        for item in reversed(event.get("payload", {}).get("configuration", [])):
            if item_name == item["name"]:
                return item
    return None


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
    assert _get_latest_telemetry_config_item(events, "_trace_sample_rate") == {
        "name": "_trace_sample_rate",
        "value": 1.0,
        "origin": "default",
    }


@pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
def test_remoteconfig_sampling_rate_telemetry(test_agent_session, run_python_code_in_subprocess):
    from ddtrace.internal.telemetry import telemetry_writer

    config._handle_remoteconfig(_base_rc_config({"tracing_sampling_rate": 0.5}))
    with tracer.trace("test") as span:
        pass
    assert span.get_metric("_dd.rule_psr") == 0.5
    telemetry_writer.periodic()

    events = test_agent_session.get_events()
    assert _get_latest_telemetry_config_item(events, "trace_sample_rate") == {
        "name": "trace_sample_rate",
        "value": 0.5,
        "origin": "remote_config",
    }
