from concurrent import futures
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


def create_mock_grpc_server():
    """Create a mock gRPC server for testing OpenTelemetry logs exporter."""
    import grpc
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2_grpc import LogsServiceServicer
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2_grpc import add_LogsServiceServicer_to_server

    class MockLogsService(LogsServiceServicer):
        def __init__(self):
            self.received_requests = []

        def Export(self, request, context):
            from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse

            self.received_requests.append(request)
            return ExportLogsServiceResponse()

    mock_service = MockLogsService()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    add_LogsServiceServicer_to_server(mock_service, server)
    server.add_insecure_port("[::]:4317")
    return mock_service, server


def decode_logs_request(request_body: bytes):
    """Decode protobuf request body into ExportLogsServiceRequest object."""
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

    export_request = ExportLogsServiceRequest()
    export_request.ParseFromString(request_body)
    return export_request


def find_log_record_by_message(captured_logs, log_message: str):
    """Find a log record and its resource by matching log message content."""
    for resource_logs in captured_logs.resource_logs:
        for scope_logs in resource_logs.scope_logs:
            for record in scope_logs.log_records:
                if log_message in record.body.string_value:
                    return record, resource_logs.resource
    return None, None


def extract_resource_attributes(log_record, resource) -> dict:
    """Extract resource attributes from log record and resource."""
    attributes = {}

    for attr in resource.attributes:
        if attr.key == "service.name":
            attributes["service"] = attr.value.string_value
        elif attr.key == "deployment.environment":
            attributes["env"] = attr.value.string_value
        elif attr.key == "service.version":
            attributes["version"] = attr.value.string_value
        elif attr.key == "host.name":
            attributes["host_name"] = attr.value.string_value

    if log_record:
        attributes["trace_id"] = log_record.trace_id.hex()
        attributes["span_id"] = log_record.span_id.hex()

    return attributes


def extract_log_correlation_attributes(captured_logs, log_message: str) -> dict:
    """Extract log correlation attributes and trace/span IDs from captured logs."""
    log_record, resource = find_log_record_by_message(captured_logs, log_message)
    return extract_resource_attributes(log_record, resource)


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

    logger_provider = get_logger_provider()
    assert (
        DD_LOGS_PROVIDER_CONFIGURED
    ), f"OpenTelemetry logs exporter should be configured automatically. {logger_provider} configured."


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

    logger_provider = get_logger_provider()
    assert (
        DD_LOGS_PROVIDER_CONFIGURED
    ), f"OpenTelemetry logs exporter should be configured automatically. {logger_provider} configured."


@pytest.mark.skipif(
    EXPORTER_VERSION < MINIMUM_SUPPORTED_VERSION,
    reason=f"OpenTelemetry exporter version {MINIMUM_SUPPORTED_VERSION} is required to export logs",
)
@pytest.mark.subprocess(ddtrace_run=True, parametrize={"DD_LOGS_OTEL_ENABLED": [None, "false"]})
def test_otel_logs_support_not_enabled():
    """Test OpenTelemetry logs exporter is not configured when DD_LOGS_OTEL_ENABLED is not set."""
    from opentelemetry._logs import get_logger_provider

    from ddtrace.internal.opentelemetry.logs import DD_LOGS_PROVIDER_CONFIGURED

    logger_provider = get_logger_provider()
    assert (
        not DD_LOGS_PROVIDER_CONFIGURED
    ), f"OpenTelemetry logs exporter should not be configured automatically. {logger_provider} configured."


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
    from unittest.mock import Mock
    from unittest.mock import patch

    from opentelemetry._logs import get_logger_provider

    from ddtrace.internal.opentelemetry.logs import HTTP_LOGS_ENDPOINT
    from tests.opentelemetry.test_logs import decode_logs_request
    from tests.opentelemetry.test_logs import extract_log_correlation_attributes

    log = getLogger()
    with patch("requests.sessions.Session.request") as mock_request:
        mock_response = Mock(status_code=200)
        mock_request.return_value = mock_response

        log.error("test_otel_logs_exporter_auto_configured_http")

        logger_provider = get_logger_provider()
        logger_provider.force_flush()

        request_body = None
        for call in mock_request.call_args_list:
            method, url = call[0][:2]
            if method == "POST" and HTTP_LOGS_ENDPOINT in url:
                request_body = call[1].get("data", None)
                break
        assert request_body is not None, (
            "Expected a request body to be present in the "
            f"OpenTelemetry logs exporter request {mock_request.call_args_list}"
        )

    captured_logs = decode_logs_request(request_body)
    assert len(captured_logs.resource_logs) > 0, "Expected at least one resource log in the OpenTelemetry logs request"

    attributes = extract_log_correlation_attributes(captured_logs, "test_otel_logs_exporter_auto_configured_http")
    assert len(attributes) == 6, f"Expected 6 log correlation attributes but found: {attributes}"
    assert (
        attributes["service"] == "ddservice"
    ), f"Expected service.name to be 'ddservice' but found: {attributes['service']}"
    assert attributes["env"] == "ddenv", f"Expected deployment.environment to be 'ddenv' but found: {attributes['env']}"
    assert attributes["version"] == "ddv1", f"Expected service.version to be 'ddv1' but found: {attributes['version']}"
    assert (
        attributes["host_name"] == "ddhost"
    ), f"Expected host.name to be 'ddhost' but found: {attributes['host_name']}"
    assert attributes["trace_id"] in (
        "00000000000000000000000000000000",
        "",
    ), f"Expected trace_id to be '00000000000000000000000000000000' but found: {attributes['trace_id']}"
    assert attributes["span_id"] in (
        "0000000000000000",
        "",
    ), f"Expected span_id to be '0000000000000000' but found: {attributes['span_id']}"


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

    from opentelemetry._logs import get_logger_provider

    from tests.opentelemetry.test_logs import create_mock_grpc_server

    mock_service, server = create_mock_grpc_server()
    try:
        server.start()
        logger = getLogger()
        logger.error("test_otel_logs_exporter_auto_configured_grpc")

        logger_provider = get_logger_provider()
        logger_provider.force_flush()
    finally:
        server.stop(0)

    assert mock_service.received_requests, "No gRPC Export requests were received by the mock server"

    all_logs = [
        record
        for request in mock_service.received_requests
        for resource_logs in request.resource_logs
        for scope_logs in resource_logs.scope_logs
        for record in scope_logs.log_records
    ]

    assert any(
        b"test_otel_logs_exporter_auto_configured_grpc" in log.body.string_value.encode() for log in all_logs
    ), "Expected log message not found in exported gRPC payload"


@pytest.mark.skipif(
    EXPORTER_VERSION < MINIMUM_SUPPORTED_VERSION,
    reason=f"OpenTelemetry exporter version {MINIMUM_SUPPORTED_VERSION} is required to export logs",
)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_LOGS_OTEL_ENABLED": "true",
        "DD_SERVICE": "test_service",
        "DD_VERSION": "1.0",
        "DD_ENV": "test_env",
    },
    parametrize={"DD_LOGS_INJECTION": [None, "true"]},
)
def test_ddtrace_log_injection_otlp_enabled():
    """Test that ddtrace log injection is disabled when OpenTelemetry logs are enabled."""
    from logging import getLogger

    from opentelemetry._logs import get_logger_provider

    from ddtrace import tracer
    from ddtrace.internal.constants import LOG_ATTR_ENV
    from ddtrace.internal.constants import LOG_ATTR_SERVICE
    from ddtrace.internal.constants import LOG_ATTR_SPAN_ID
    from ddtrace.internal.constants import LOG_ATTR_TRACE_ID
    from ddtrace.internal.constants import LOG_ATTR_VERSION
    from tests.opentelemetry.test_logs import create_mock_grpc_server
    from tests.opentelemetry.test_logs import find_log_record_by_message

    log = getLogger()
    mock_service, server = create_mock_grpc_server()

    try:
        server.start()
        with tracer.trace("test_trace"):
            log.error("test_ddtrace_log_correlation")
        logger_provider = get_logger_provider()
        logger_provider.force_flush()
    finally:
        server.stop(0)

    log_record = None
    for request in mock_service.received_requests:
        log_record, _ = find_log_record_by_message(request, "test_ddtrace_log_correlation")
        if log_record:
            break
    else:
        assert False, f"No log record with message 'test_ddtrace_log_correlation' found in the request: {request}"

    ddtrace_attributes = {}
    for attr in log_record.attributes:
        if attr.key in (LOG_ATTR_ENV, LOG_ATTR_SERVICE, LOG_ATTR_VERSION, LOG_ATTR_TRACE_ID, LOG_ATTR_SPAN_ID):
            ddtrace_attributes[attr.key] = attr.value

    assert (
        ddtrace_attributes == {}
    ), f"Log Injection attributes should not be present in the log record: {ddtrace_attributes}"


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

    from opentelemetry._logs import get_logger_provider

    from ddtrace import tracer
    from tests.opentelemetry.test_logs import create_mock_grpc_server
    from tests.opentelemetry.test_logs import extract_log_correlation_attributes

    otel_context = os.environ.get("OTEL_PYTHON_CONTEXT")
    assert (
        otel_context == "ddcontextvars_context"
    ), f"Expected OTEL_PYTHON_CONTEXT to be set to ddcontextvars_context but found: {otel_context}"

    log = getLogger()
    mock_service, server = create_mock_grpc_server()
    try:
        server.start()
        with tracer.trace("test_trace") as span:
            log.error("test_ddtrace_log_correlation")
        logger_provider = get_logger_provider()
        logger_provider.force_flush()
    finally:
        server.stop(0)

    attributes = {}
    for request in mock_service.received_requests:
        attributes = extract_log_correlation_attributes(request, "test_ddtrace_log_correlation")
        if attributes:
            break

    assert len(attributes) == 6, f"All log correlation attributes NOT found attributes: {attributes}"
    assert (
        attributes["service"] == "test_service"
    ), f"Expected service.name to be 'test_service' but found: {attributes['service']}"
    assert (
        attributes["env"] == "test_env"
    ), f"Expected deployment.environment to be 'test_env' but found: {attributes['env']}"
    assert attributes["version"] == "1.0", f"Expected service.version to be '1.0' but found: {attributes['version']}"
    assert (
        attributes["host_name"] == "test_host2"
    ), f"Expected host.name to match 'test_host2' but found: {attributes['host_name']}"
    assert (
        int(attributes["trace_id"], 16) == span.trace_id
    ), f"Expected trace_id_hex to be set to {attributes['trace_id']} but found: {span.trace_id}"
    assert (
        int(attributes["span_id"], 16) == span.span_id
    ), f"Expected span_id_hex to be set to {attributes['span_id']} but found: {span.span_id}"


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

    from opentelemetry import trace
    from opentelemetry._logs import get_logger_provider

    from tests.opentelemetry.test_logs import create_mock_grpc_server
    from tests.opentelemetry.test_logs import extract_log_correlation_attributes

    otel_context = os.environ.get("OTEL_PYTHON_CONTEXT")
    assert (
        otel_context == "ddcontextvars_context"
    ), f"Expected OTEL_PYTHON_CONTEXT to be set to ddcontextvars_context but found: {otel_context}"

    log = getLogger()

    mock_service, server = create_mock_grpc_server()
    try:
        server.start()
        oteltracer = trace.get_tracer(__name__)
        with oteltracer.start_as_current_span("test-otel-distributed-trace") as ot_span:
            log.error("test_otel_trace_log_correlation")
        logger_provider = get_logger_provider()
        logger_provider.force_flush()
    finally:
        server.stop(0)

    attributes = {}
    for request in mock_service.received_requests:
        attributes = extract_log_correlation_attributes(request, "test_otel_trace_log_correlation")
        if attributes:
            break

    assert len(attributes) == 6, f"All log correlation attributes NOT found attributes: {attributes}"
    assert (
        attributes["service"] == "test_service"
    ), f"Expected service.name to be 'test_service' but found: {attributes['service']}"
    assert (
        attributes["env"] == "test_env"
    ), f"Expected deployment.environment to be 'test_env' but found: {attributes['env']}"
    assert attributes["version"] == "1.0", f"Expected service.version to be '1.0' but found: {attributes['version']}"
    assert (
        attributes["host_name"] == "test_host"
    ), f"Expected host.name to match 'test_host' but found: {attributes['host_name']}"

    span_context = ot_span.get_span_context()
    assert (
        int(attributes["trace_id"], 16) == span_context.trace_id
    ), f"Expected trace_id_hex to be set to {attributes['trace_id']} but found: {span_context.trace_id}"
    assert (
        int(attributes["span_id"], 16) == span_context.span_id
    ), f"Expected span_id_hex to be set to {attributes['span_id']} but found: {span_context.span_id}"
