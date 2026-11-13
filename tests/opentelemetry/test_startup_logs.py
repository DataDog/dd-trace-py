import json
import os
import subprocess
import sys

import pytest


_MARKER = "- DATADOG TRACER CONFIGURATION - "

# Keys every startup configuration reports, whether or not the corresponding setting is configured.
_OTEL_KEYS = frozenset(
    (
        "DD_TRACE_OTEL_ENABLED",
        "DD_TRACE_OTEL_SEMANTICS_ENABLED",
        "OTEL_TRACES_EXPORTER",
        "OTEL_TRACES_SAMPLER",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
        "DD_LOGS_OTEL_ENABLED",
        "OTEL_LOGS_EXPORTER",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
        "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
        "DD_METRICS_OTEL_ENABLED",
        "OTEL_METRIC_EXPORT_INTERVAL",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
        "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_LOG_LEVEL",
        "OTEL_SERVICE_NAME",
        "OTEL_RESOURCE_ATTRIBUTES",
        "OTEL_PROPAGATORS",
    )
)


def _startup_configurations(env_overrides):
    """Start a ddtrace process and return the configurations its startup logs report."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DD_", "OTEL_"))}
    env.update(
        {
            "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
            "DD_REMOTE_CONFIGURATION_ENABLED": "false",
            # Nothing listens there, so the tracer neither reaches an Agent nor waits on DNS.
            "DD_TRACE_AGENT_URL": "http://127.0.0.1:1",
        }
    )
    env.update(env_overrides)
    process = subprocess.run(
        [sys.executable, "-c", "import ddtrace.auto"], env=env, capture_output=True, text=True, timeout=60
    )
    assert process.returncode == 0, process.stderr
    return [json.loads(line.split(_MARKER, 1)[1]) for line in process.stderr.splitlines() if _MARKER in line]


def test_startup_logs_report_otel_configuration():
    """The startup log is JSON that reports the effective OpenTelemetry configuration."""
    (configuration,) = _startup_configurations(
        {
            "DD_TRACE_STARTUP_LOGS": "true",
            "DD_SERVICE": "otel-startup",
            "DD_TRACE_OTEL_ENABLED": "true",
            "DD_LOGS_OTEL_ENABLED": "true",
            "DD_METRICS_OTEL_ENABLED": "true",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_TRACES_SAMPLER": "parentbased_always_on",
            "OTEL_PROPAGATORS": "tracecontext,baggage",
            # Not the agent host, which the logs and metrics endpoints would otherwise default to.
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:4318/v1/traces",
            "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "delta",
            "OTEL_METRIC_EXPORT_INTERVAL": "5000",
        }
    )

    assert _OTEL_KEYS <= configuration.keys(), _OTEL_KEYS - configuration.keys()
    assert configuration["DD_TRACE_OTEL_ENABLED"] is True
    assert configuration["DD_LOGS_OTEL_ENABLED"] is True
    assert configuration["DD_METRICS_OTEL_ENABLED"] is True
    assert configuration["OTEL_TRACES_EXPORTER"] == "otlp"
    assert configuration["OTEL_LOGS_EXPORTER"] == "otlp"
    assert configuration["OTEL_TRACES_SAMPLER"] == "parentbased_always_on"
    assert "OTEL_TRACES_SAMPLER_ARG" not in configuration
    assert configuration["OTEL_PROPAGATORS"] == "tracecontext,baggage"
    assert configuration["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://localhost:4318"
    assert configuration["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert configuration["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == "http://127.0.0.1:4318/v1/traces"
    assert configuration["OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"] == "http/protobuf"
    # Without signal-specific endpoints, the logs and metrics exporters derive theirs from the global one.
    assert configuration["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] == "http://localhost:4318/v1/logs"
    assert configuration["OTEL_EXPORTER_OTLP_LOGS_PROTOCOL"] == "http/protobuf"
    assert configuration["OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"] == "http://localhost:4318/v1/metrics"
    assert configuration["OTEL_EXPORTER_OTLP_METRICS_PROTOCOL"] == "http/protobuf"
    assert configuration["OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"] == "delta"
    assert configuration["OTEL_METRIC_EXPORT_INTERVAL"] == 5000
    assert configuration["OTEL_SERVICE_NAME"] == "otel-startup"
    assert configuration["OTEL_RESOURCE_ATTRIBUTES"]["service.name"] == "otel-startup"
    # DD_TRACE_STARTUP_LOGS emits the configuration although the ddtrace logger only emits warnings.
    assert configuration["OTEL_LOG_LEVEL"] == "warn"


def test_startup_logs_report_otel_defaults():
    (configuration,) = _startup_configurations({"DD_TRACE_STARTUP_LOGS": "true"})

    assert _OTEL_KEYS <= configuration.keys(), _OTEL_KEYS - configuration.keys()
    assert configuration["DD_TRACE_OTEL_ENABLED"] is False
    assert configuration["DD_TRACE_OTEL_SEMANTICS_ENABLED"] is False
    assert configuration["DD_LOGS_OTEL_ENABLED"] is False
    assert configuration["DD_METRICS_OTEL_ENABLED"] is False
    assert configuration["OTEL_TRACES_EXPORTER"] == "none"
    assert configuration["OTEL_LOGS_EXPORTER"] == "none"
    assert configuration["OTEL_TRACES_SAMPLER"] == "datadog_agent_rates"
    assert configuration["OTEL_PROPAGATORS"] == "datadog,tracecontext,baggage"
    assert configuration["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert configuration["OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"] == "http/protobuf"
    assert configuration["OTEL_EXPORTER_OTLP_LOGS_PROTOCOL"] == "http/protobuf"
    assert configuration["OTEL_EXPORTER_OTLP_METRICS_PROTOCOL"] == "http/protobuf"
    assert configuration["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"].endswith(":4318/v1/traces")
    assert configuration["OTEL_EXPORTER_OTLP_TRACES_HEADERS"] == {}
    assert configuration["OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"] == "delta"
    assert configuration["OTEL_METRIC_EXPORT_INTERVAL"] == 10000


def test_startup_logs_debug_mode_reports_otel_log_level():
    (configuration,) = _startup_configurations({"DD_TRACE_DEBUG": "true"})

    assert configuration["OTEL_LOG_LEVEL"] == "debug"


def test_startup_logs_disabled():
    assert _startup_configurations({"DD_TRACE_STARTUP_LOGS": "false"}) == []


def test_startup_logs_redact_otlp_credentials():
    (configuration,) = _startup_configurations(
        {
            "DD_TRACE_STARTUP_LOGS": "true",
            "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer secret-token",
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "dd-api-key=secret-key, X-Custom = secret-value",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://user:secret-password@127.0.0.1:4318/v1/traces?k=secret#s",
        }
    )

    assert "secret" not in json.dumps(configuration)
    assert configuration["OTEL_EXPORTER_OTLP_HEADERS"] == {"authorization": "<redacted>"}
    assert configuration["OTEL_EXPORTER_OTLP_TRACES_HEADERS"] == {
        "dd-api-key": "<redacted>",
        "x-custom": "<redacted>",
    }
    # The logs and metrics exporters fall back on the global headers.
    assert configuration["OTEL_EXPORTER_OTLP_LOGS_HEADERS"] == {"authorization": "<redacted>"}
    assert configuration["OTEL_EXPORTER_OTLP_METRICS_HEADERS"] == {"authorization": "<redacted>"}
    assert configuration["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == "http://127.0.0.1:4318/v1/traces"


@pytest.mark.subprocess(
    env={"DD_TRACE_PROPAGATION_STYLE_INJECT": "datadog", "DD_TRACE_PROPAGATION_STYLE_EXTRACT": "tracecontext"},
)
def test_otel_propagators_unset_when_injection_and_extraction_differ():
    from ddtrace.internal import debug
    from ddtrace.internal.utils.tracer_debug_info import TracerDebugInfo
    from ddtrace.trace import tracer

    assert debug.collect(TracerDebugInfo.from_tracer(tracer))["OTEL_PROPAGATORS"] is None


@pytest.mark.subprocess(env={"OTEL_PROPAGATORS": "none"})
def test_otel_propagators_none():
    from ddtrace.internal import debug
    from ddtrace.internal.utils.tracer_debug_info import TracerDebugInfo
    from ddtrace.trace import tracer

    assert debug.collect(TracerDebugInfo.from_tracer(tracer))["OTEL_PROPAGATORS"] == "none"


@pytest.mark.subprocess(env={"OTEL_TRACES_EXPORTER": "otlp", "DD_TRACE_ENABLED": "false"})
def test_otel_traces_exporter_none_when_tracing_is_disabled():
    from ddtrace.internal import debug
    from ddtrace.internal.utils.tracer_debug_info import TracerDebugInfo
    from ddtrace.trace import tracer

    assert debug.collect(TracerDebugInfo.from_tracer(tracer))["OTEL_TRACES_EXPORTER"] == "none"


@pytest.mark.parametrize(
    "rules,expected",
    [
        ([], ("datadog_agent_rates", None)),
        ([{"sample_rate": 1.0}], ("parentbased_always_on", None)),
        ([{"sample_rate": 0.0}], ("parentbased_always_off", None)),
        ([{"sample_rate": 0.25}], ("parentbased_traceidratio", 0.25)),
        # The first rule matches every span, so the following rules never apply.
        ([{"sample_rate": 0.5}, {"sample_rate": 0.1, "service": "web"}], ("parentbased_traceidratio", 0.5)),
        ([{"sample_rate": 1.0, "service": "web"}], ("datadog_custom_rules", None)),
        ([{"sample_rate": 1.0, "name": "http.request"}], ("datadog_custom_rules", None)),
        ([{"sample_rate": 1.0, "resource": "GET /"}], ("datadog_custom_rules", None)),
        ([{"sample_rate": 1.0, "tags": {"k": "v"}}], ("datadog_custom_rules", None)),
    ],
)
def test_otel_traces_sampler(rules, expected):
    from ddtrace._trace.sampling_rule import SamplingRule
    from ddtrace.internal.debug import _otel_traces_sampler

    assert _otel_traces_sampler([SamplingRule(**rule) for rule in rules]) == expected


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        (None, None),
        ("", ""),
        ("http://collector:4318", "http://collector:4318"),
        ("https://collector:4318/v1/logs", "https://collector:4318/v1/logs"),
        ("http://user:password@collector:4318/v1/logs?token=t#f", "http://collector:4318/v1/logs"),
        ("http://[::1]:4317", "http://[::1]:4317"),
        ("collector:4317", "collector:4317"),
        ("user@collector:4317?token=t", "collector:4317"),
        ("http://[::1", None),
    ],
)
def test_sanitize_otlp_endpoint(endpoint, expected):
    from ddtrace.internal.debug import _sanitize_otlp_endpoint

    assert _sanitize_otlp_endpoint(endpoint) == expected


@pytest.mark.parametrize(
    "headers,expected",
    [
        (None, {}),
        ("", {}),
        ("key", {}),
        ("=value", {}),
        ("Key=value", {"key": "<redacted>"}),
        (" a = 1 ,b=2=3, c=", {"a": "<redacted>", "b": "<redacted>", "c": "<redacted>"}),
    ],
)
def test_redact_otlp_headers(headers, expected):
    from ddtrace.internal.debug import _redact_otlp_headers

    assert _redact_otlp_headers(headers) == expected
