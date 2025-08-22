import base64
import os

from opentelemetry.version import __version__ as api_version_string
import pytest

from ddtrace.internal.opentelemetry.logs import API_VERSION
from ddtrace.internal.opentelemetry.logs import MINIMUM_SUPPORTED_VERSION


try:
    from opentelemetry.exporter.otlp.version import __version__ as exporter_version

    EXPORTER_VERSION = tuple(int(x) for x in exporter_version.split(".")[:3])
except ImportError:
    EXPORTER_VERSION = (0, 0, 0)


def find_log_correlation_attributes(captured_logs, log_message: str):
    """Find and return the log correlation attributes from the received requests."""
    lc_attributes = {}
    for resource_logs in captured_logs["resource_logs"]:
        for attr in resource_logs["resource"]["attributes"]:
            key = attr["key"]
            value_str = attr["value"]["string_value"]
            if key == "service.name":
                lc_attributes["service"] = value_str
            elif key == "deployment.environment":
                lc_attributes["env"] = value_str
            elif key == "service.version":
                lc_attributes["version"] = value_str
            elif key == "host.name":
                lc_attributes["host_name"] = value_str
        for scope_logs in resource_logs["scope_logs"]:
            for record in scope_logs["log_records"]:
                if log_message in record["body"]["string_value"]:
                    if "trace_id" in record:
                        lc_attributes["trace_id"] = base64.b64decode(record["trace_id"]).hex()
                    if "span_id" in record:
                        lc_attributes["span_id"] = base64.b64decode(record["span_id"]).hex()
                    break
    return lc_attributes


@pytest.mark.skipif(API_VERSION >= (1, 15, 0), reason="OpenTelemetry API >= 1.15.0 supports logs collection")
def test_otel_api_version_not_supported(ddtrace_run_python_code_in_subprocess):
    """Test error when OpenTelemetry API version is too old."""
    env = os.environ.copy()
    env["DD_LOGS_OTEL_ENABLED"] = "true"
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code="", env=env)
    assert status == 0, (stdout, stderr)
    assert (
        "OpenTelemetry API requires version 1.15.0 or higher to enable logs collection. "
        f"Found version {api_version_string}. Please upgrade the opentelemetry-api package before "
        "enabling ddtrace OpenTelemetry Logs support.\n"
    ) in stderr.decode()


@pytest.mark.skipif(API_VERSION < (1, 15, 0), reason="OpenTelemetry API >= 1.15.0 supports logs collection")
@pytest.mark.skipif(
    EXPORTER_VERSION != (0, 0, 0), reason="Test requires OpenTelemetry SDK and Exporter to not be installed"
)
def test_otel_sdk_not_installed(ddtrace_run_python_code_in_subprocess):
    """Test error when OpenTelemetry SDK is not installed."""
    env = os.environ.copy()
    env["DD_LOGS_OTEL_ENABLED"] = "true"
    stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code="", env=env)
    assert status == 0, (stdout, stderr)

    assert (
        "OpenTelemetry SDK is not installed, opentelemetry logs will not be enabled. "
        "Please install the OpenTelemetry SDK before enabling ddtrace OpenTelemetry Logs support."
    ) in stderr.decode(), f"Expected error message not found in stderr: {stderr.decode()}"


@pytest.mark.skipif(
    EXPORTER_VERSION < MINIMUM_SUPPORTED_VERSION,
    reason=f"OpenTelemetry exporter version {MINIMUM_SUPPORTED_VERSION} is required to export logs",
)
@pytest.mark.subprocess(ddtrace_run=True, env={"DD_LOGS_OTEL_ENABLED": "true"})
def test_otel_logs_support_enabled():
    """Test OpenTelemetry logs exporter auto-configuration when DD_LOGS_OTEL_ENABLED is set."""
    from opentelemetry._logs import get_logger_provider

    from ddtrace.internal.opentelemetry.logs import DD_LOGS_PROVIDER_CONFIGURED

    lp = get_logger_provider()
    assert (
        DD_LOGS_PROVIDER_CONFIGURED
    ), f"OpenTelemetry logs exporter should be configured automatically. {lp} configured."


@pytest.mark.skipif(
    EXPORTER_VERSION < MINIMUM_SUPPORTED_VERSION,
    reason=f"OpenTelemetry exporter version {MINIMUM_SUPPORTED_VERSION} is required to export logs",
)
@pytest.mark.subprocess(
    env={"DD_LOGS_OTEL_ENABLED": "true"},
    err=b"OpenTelemetry Logs exporter was already configured by ddtrace, skipping setup.\n",
)
def test_otel_logs_exporter_configured_twice():
    """Test OpenTelemetry logs exporter handles multiple configuration calls gracefully."""
    from ddtrace.internal.opentelemetry.logs import set_otel_logs_provider

    set_otel_logs_provider()
    set_otel_logs_provider()

    from opentelemetry._logs import get_logger_provider

    from ddtrace.internal.opentelemetry.logs import DD_LOGS_PROVIDER_CONFIGURED

    lp = get_logger_provider()
    assert (
        DD_LOGS_PROVIDER_CONFIGURED
    ), f"OpenTelemetry logs exporter should be configured automatically. {lp} configured."


@pytest.mark.skipif(
    EXPORTER_VERSION < MINIMUM_SUPPORTED_VERSION,
    reason=f"OpenTelemetry exporter version {MINIMUM_SUPPORTED_VERSION} is required to export logs",
)
@pytest.mark.subprocess(ddtrace_run=True, parametrize={"DD_LOGS_OTEL_ENABLED": [None, "false"]})
def test_otel_logs_support_not_enabled():
    """Test OpenTelemetry logs exporter is not configured when DD_LOGS_OTEL_ENABLED is not set."""
    from opentelemetry._logs import get_logger_provider

    from ddtrace.internal.opentelemetry.logs import DD_LOGS_PROVIDER_CONFIGURED

    lp = get_logger_provider()
    assert (
        not DD_LOGS_PROVIDER_CONFIGURED
    ), f"OpenTelemetry logs exporter should not be configured automatically. {lp} configured."


@pytest.mark.skipif(
    EXPORTER_VERSION < MINIMUM_SUPPORTED_VERSION,
    reason=f"OpenTelemetry exporter version {MINIMUM_SUPPORTED_VERSION} is required to use the HTTP exporter",
)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_LOGS_OTEL_ENABLED": "true",
        "DD_SERVICE": "ddservice",
        "DD_VERSION": "ddv1",
        "DD_ENV": "ddenv",
        "DD_TRACE_REPORT_HOSTNAME": "true",
        "DD_HOSTNAME": "ddhost",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    },
    err=None,
)
def test_otel_logs_exporter_auto_configured_http():
    """Test OpenTelemetry logs exporter auto-configuration for HTTP protocol."""
    from logging import getLogger

    from ddapm_test_agent.client import TestOTLPClient
    from opentelemetry._logs import get_logger_provider

    client = TestOTLPClient()
    client.clear()

    log = getLogger()
    log.error("test_otel_logs_exporter_auto_configured_http")

    lp = get_logger_provider()
    lp.shutdown()

    captured_logs = client.wait_for_num_logs(1)
    expected_log = [
        record["body"]["string_value"]
        for resource_logs in captured_logs[0]["resource_logs"]
        for scope_logs in resource_logs["scope_logs"]
        for record in scope_logs["log_records"]
        if record["body"]["string_value"] == "test_otel_logs_exporter_auto_configured_http"
    ]
    assert (
        len(expected_log) == 1
    ), f"Expected 1 log with message 'test_otel_logs_exporter_auto_configured_http' but found: {captured_logs}"


@pytest.mark.skipif(
    EXPORTER_VERSION < MINIMUM_SUPPORTED_VERSION,
    reason=f"OpenTelemetry exporter version {MINIMUM_SUPPORTED_VERSION} is required to use the OTLP Logs exporters",
)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_LOGS_OTEL_ENABLED": "true",
        "DD_LOGS_INJECTION": "false",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
    },
    err=b"OpenTelemetry Logs exporter protocol 'http/json' is not supported. Use 'grpc' or 'http/protobuf'.\n",
)
def test_otel_logs_exporter_otlp_protocol_unsupported():
    import opentelemetry._logs  # noqa: F401


@pytest.mark.skipif(
    EXPORTER_VERSION < MINIMUM_SUPPORTED_VERSION,
    reason=f"OpenTelemetry exporter version {MINIMUM_SUPPORTED_VERSION} is required to export logs",
)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={"DD_LOGS_OTEL_ENABLED": "true"},
    parametrize={"OTEL_EXPORTER_OTLP_PROTOCOL": ["grpc", None]},
    err=None,
)
def test_otel_logs_exporter_auto_configured_grpc():
    """Test OpenTelemetry logs exporter sends data via gRPC to mocked OTLP endpoint."""
    from logging import getLogger

    from ddapm_test_agent.client import TestOTLPClient
    from opentelemetry._logs import get_logger_provider

    client = TestOTLPClient()
    client.clear()

    logger = getLogger()
    logger.error("test_otel_logs_exporter_auto_configured_grpc")
    lp = get_logger_provider()
    lp.shutdown()

    captured_logs = client.wait_for_num_logs(1)
    expected_log = [
        record["body"]["string_value"]
        for resource_logs in captured_logs[0]["resource_logs"]
        for scope_logs in resource_logs["scope_logs"]
        for record in scope_logs["log_records"]
        if record["body"]["string_value"] == "test_otel_logs_exporter_auto_configured_grpc"
    ]
    assert (
        len(expected_log) == 1
    ), f"Expected 1 log with message 'test_otel_logs_exporter_auto_configured_grpc' but found: {captured_logs}"


@pytest.mark.skipif(
    EXPORTER_VERSION < MINIMUM_SUPPORTED_VERSION,
    reason=f"OpenTelemetry exporter version {MINIMUM_SUPPORTED_VERSION} is required to export logs",
)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_LOGS_OTEL_ENABLED": "true",
        "DD_TRACE_OTEL_ENABLED": None,
        "DD_SERVICE": "test_service",
        "DD_VERSION": "1.0",
        "DD_ENV": "test_env",
        "DD_HOSTNAME": "test_host",
        "DD_TRACE_REPORT_HOSTNAME": "true",
        "OTEL_RESOURCE_ATTRIBUTES": "service.name=test_service2,service.version=2.0,"
        "deployment.environment=test_env2,host.name=test_host2",
    },
)
def test_ddtrace_log_correlation():
    """Test OpenTelemetry logs exporter correlates ddtrace traces with logs."""
    from logging import getLogger
    import os

    from ddapm_test_agent.client import TestOTLPClient
    from opentelemetry._logs import get_logger_provider

    from ddtrace import tracer
    from tests.opentelemetry.test_logs import find_log_correlation_attributes

    otel_context = os.environ.get("OTEL_PYTHON_CONTEXT")
    assert (
        otel_context == "ddcontextvars_context"
    ), f"Expected OTEL_PYTHON_CONTEXT to be set to ddcontextvars_context but found: {otel_context}"

    client = TestOTLPClient()
    client.clear()

    log = getLogger()
    with tracer.trace("test_ddtrace_log_correlation") as span:
        log.error("test_ddtrace_log_correlation")
    lp = get_logger_provider()
    lp.shutdown()

    captured_logs = client.wait_for_num_logs(1)
    lc_attributes = find_log_correlation_attributes(captured_logs[0], "test_ddtrace_log_correlation")

    assert len(lc_attributes) == 6, f"All log correlation attributes NOT found lc_attributes: {lc_attributes}"
    assert (
        lc_attributes["service"] == "test_service"
    ), f"Expected service.name to be 'test_service' but found: {lc_attributes['service']}"
    assert (
        lc_attributes["env"] == "test_env"
    ), f"Expected deployment.environment to be 'test_env' but found: {lc_attributes['env']}"
    assert (
        lc_attributes["version"] == "1.0"
    ), f"Expected service.version to be '1.0' but found: {lc_attributes['version']}"
    assert (
        lc_attributes["host_name"] == "test_host2"
    ), f"Expected host.name to match 'test_host2' but found: {lc_attributes['host_name']}"
    expected_trace_id = int(lc_attributes["trace_id"], 16)
    assert (
        expected_trace_id == span.trace_id
    ), f"Expected trace_id_hex to be set to {expected_trace_id} but found: {span.trace_id}"
    expected_span_id = int(lc_attributes["span_id"], 16)
    assert (
        expected_span_id == span.span_id
    ), f"Expected span_id_hex to be set to {expected_span_id} but found: {span.span_id}"


@pytest.mark.skipif(
    EXPORTER_VERSION < MINIMUM_SUPPORTED_VERSION,
    reason=f"OpenTelemetry exporter version {MINIMUM_SUPPORTED_VERSION} is required to export logs",
)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_LOGS_OTEL_ENABLED": "true",
        "DD_TRACE_OTEL_ENABLED": "true",
        "OTEL_RESOURCE_ATTRIBUTES": "service.name=test_service,service.version=1.0,"
        "deployment.environment=test_env,host.name=test_host",
    },
)
def test_otel_trace_log_correlation():
    """Test OpenTelemetry logs exporter correlates OpenTelemetry traces with logs."""
    from logging import getLogger
    import os

    from ddapm_test_agent.client import TestOTLPClient
    from opentelemetry import trace
    from opentelemetry._logs import get_logger_provider

    from tests.opentelemetry.test_logs import find_log_correlation_attributes

    otel_context = os.environ.get("OTEL_PYTHON_CONTEXT")
    assert (
        otel_context == "ddcontextvars_context"
    ), f"Expected OTEL_PYTHON_CONTEXT to be set to ddcontextvars_context but found: {otel_context}"

    client = TestOTLPClient()
    client.clear()

    log = getLogger()

    oteltracer = trace.get_tracer(__name__)
    with oteltracer.start_as_current_span("test-otel-distributed-trace") as ot_span:
        log.error("test_otel_trace_log_correlation")
    lp = get_logger_provider()
    lp.shutdown()

    captured_logs = client.wait_for_num_logs(1)
    lc_attributes = find_log_correlation_attributes(captured_logs[0], "test_otel_trace_log_correlation")

    assert len(lc_attributes) == 6, f"All log correlation attributes NOT found lc_attributes: {lc_attributes}"
    assert (
        lc_attributes["service"] == "test_service"
    ), f"Expected service.name to be 'test_service' but found: {lc_attributes['service']}"
    assert (
        lc_attributes["env"] == "test_env"
    ), f"Expected deployment.environment to be 'test_env' but found: {lc_attributes['env']}"
    assert (
        lc_attributes["version"] == "1.0"
    ), f"Expected service.version to be '1.0' but found: {lc_attributes['version']}"
    assert (
        lc_attributes["host_name"] == "test_host"
    ), f"Expected host.name to match 'test_host' but found: {lc_attributes['host_name']}"

    span_context = ot_span.get_span_context()
    assert (
        int(lc_attributes["trace_id"], 16) == span_context.trace_id
    ), f"Expected trace_id_hex to be set to {lc_attributes['trace_id']} but found: {span_context.trace_id}"
    assert (
        int(lc_attributes["span_id"], 16) == span_context.span_id
    ), f"Expected span_id_hex to be set to {lc_attributes['span_id']} but found: {span_context.span_id}"
