import os
from unittest import mock

import pytest


@pytest.mark.subprocess(env={"DD_TRACE_NATIVE_SPAN_EVENTS": None, "OTEL_TRACES_EXPORTER": None})
def test_trace_native_span_events_default_false():
    from ddtrace.internal.settings._agent import config

    assert config.trace_native_span_events is False


@pytest.mark.subprocess(env={"OTEL_TRACES_EXPORTER": "otlp", "DD_TRACE_NATIVE_SPAN_EVENTS": None}, err=None)
def test_trace_native_span_events_auto_enabled_with_otlp_exporter():
    from ddtrace.internal.settings._agent import config

    assert config.trace_native_span_events is True


@pytest.mark.subprocess(env={"OTEL_TRACES_EXPORTER": "OTLP", "DD_TRACE_NATIVE_SPAN_EVENTS": None}, err=None)
def test_trace_native_span_events_auto_enabled_case_insensitive():
    from ddtrace.internal.settings._agent import config

    assert config.trace_native_span_events is True


@pytest.mark.subprocess(env={"OTEL_TRACES_EXPORTER": "none", "DD_TRACE_NATIVE_SPAN_EVENTS": None})
def test_trace_native_span_events_not_enabled_with_non_otlp_exporter():
    from ddtrace.internal.settings._agent import config

    assert config.trace_native_span_events is False


@pytest.mark.subprocess(env={"OTEL_TRACES_EXPORTER": "otlp", "DD_TRACE_NATIVE_SPAN_EVENTS": "false"}, err=None)
def test_trace_native_span_events_otlp_always_enabled():
    from ddtrace.internal.settings._agent import config

    assert config.trace_native_span_events is True


@pytest.mark.subprocess(env={"OTEL_TRACES_EXPORTER": None, "DD_TRACE_NATIVE_SPAN_EVENTS": "true"}, err=None)
def test_trace_native_span_events_user_true_without_otlp():
    from ddtrace.internal.settings._agent import config

    assert config.trace_native_span_events is True


@pytest.mark.parametrize("exporters", ["otlp,zipkin", "console,otlp", " OTLP , console ", "none,otlp", "otlp,"])
def test_trace_otlp_export_enabled_with_otlp_in_exporter_list(run_python_code_in_subprocess, exporters):
    env = os.environ.copy()
    env.pop("DD_TRACE_NATIVE_SPAN_EVENTS", None)
    env["OTEL_TRACES_EXPORTER"] = exporters
    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace.internal.settings._agent import config

assert config.trace_otlp_export_enabled is True
assert config.trace_native_span_events is True
""",
        env=env,
    )
    assert status == 0, (out, err)


@pytest.mark.parametrize("exporters", ["console", "zipkin,console", "none,console", "", " , "])
def test_trace_otlp_export_disabled_without_otlp_in_exporter_list(run_python_code_in_subprocess, exporters):
    env = os.environ.copy()
    env.pop("DD_TRACE_NATIVE_SPAN_EVENTS", None)
    env["OTEL_TRACES_EXPORTER"] = exporters
    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace.internal.settings._agent import config

assert config.trace_otlp_export_enabled is False
assert config.trace_native_span_events is False
""",
        env=env,
    )
    assert status == 0, (out, err)


@pytest.mark.subprocess(
    env={"OTEL_TRACES_EXPORTER": "otlp", "DD_TRACE_AGENT_PROTOCOL_VERSION": "v0.4", "DD_TRACE_NATIVE_SPAN_EVENTS": None}
)
def test_trace_native_span_events_not_forced_when_protocol_version_overrides_otlp():
    # When DD_TRACE_AGENT_PROTOCOL_VERSION is set, the writer uses the non-OTLP path,
    # so OTEL_TRACES_EXPORTER=otlp must not force native span events on.
    from ddtrace.internal.settings._agent import config

    assert config.trace_native_span_events is False


@pytest.mark.subprocess(env={"OTEL_TRACES_EXPORTER": "otlp", "DD_TRACE_API_VERSION": "v0.4"})
def test_trace_native_span_events_not_forced_when_api_version_overrides_otlp():
    from ddtrace.internal.settings._agent import config

    assert config.trace_otlp_export_enabled is False
    assert config.trace_native_span_events is False


@pytest.mark.parametrize("source_name", ("LOCAL_CONFIG", "FLEET_CONFIG"))
def test_trace_native_span_events_not_forced_when_stable_api_version_overrides_otlp(source_name):
    from ddtrace.internal.settings import _core as settings_core
    from ddtrace.internal.settings._agent import AgentConfig

    source = getattr(settings_core, source_name)
    with (
        mock.patch.dict(source, {"DD_TRACE_API_VERSION": "v0.4"}),
        mock.patch.dict(os.environ, {"OTEL_TRACES_EXPORTER": "otlp", "DD_TRACE_OTEL_SEMANTICS_ENABLED": "false"}),
    ):
        config = AgentConfig()

    assert config.trace_otlp_export_enabled is False
    assert config.trace_native_span_events is False


@pytest.mark.subprocess(
    env={
        "DD_TRACE_OTEL_SEMANTICS_ENABLED": "true",
        "OTEL_TRACES_EXPORTER": "none",
        "DD_TRACE_AGENT_PROTOCOL_VERSION": "v0.4",
        "DD_TRACE_API_VERSION": "v0.4",
        "DD_TRACE_NATIVE_SPAN_EVENTS": None,
    }
)
def test_trace_native_span_events_otel_semantics_override_agent_protocol():
    from ddtrace.internal.settings._agent import config

    assert config.trace_otlp_export_enabled is True
    assert config.trace_native_span_events is True
