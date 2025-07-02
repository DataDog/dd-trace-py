from concurrent import futures
import os

from opentelemetry import version
import pytest


OTEL_VERSION = tuple(int(x) for x in version.__version__.split(".")[:3])


def mock_grpc_exporter_connection():
    """
    Mock the gRPC connection for OpenTelemetry logs exporter and return the MockLogsService.
    """
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
    # Start gRPC server in background thread
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    add_LogsServiceServicer_to_server(mock_service, server)
    _ = server.add_insecure_port("[::]:4317")
    return mock_service, server


def decode_logs_request(request_body: bytes):
    """
    Decode the protobuf request body into an ExportLogsServiceRequest object.
    """
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

    export_request = ExportLogsServiceRequest()
    export_request.ParseFromString(request_body)
    return export_request


def find_log_correlation_attributes(captured_logs, log_message: str) -> dict[str, str]:
    """Find and return the log correlation attributes from the received requests."""
    lc_attributes = {}
    for resource_logs in captured_logs.resource_logs:
        for attr in resource_logs.resource.attributes:
            if attr.key == "service.name":
                lc_attributes["service"] = attr.value.string_value
            elif attr.key == "deployment.environment":
                lc_attributes["env"] = attr.value.string_value
            elif attr.key == "service.version":
                lc_attributes["version"] = attr.value.string_value
            elif attr.key == "host.name":
                lc_attributes["host_name"] = attr.value.string_value
        for scope_logs in resource_logs.scope_logs:
            for record in scope_logs.log_records:
                if log_message in record.body.string_value:
                    lc_attributes["trace_id"] = record.trace_id.hex()
                    lc_attributes["span_id"] = record.span_id.hex()
                    break
    return lc_attributes


def skipif(exporter_installed: bool = False, exporter_not_installed: bool = False):
    """
    Returns a pytest skip marker based on OpenTelemetry version and exporter installation.

    Parameters:
    - exporter_installed: If True, skip tests that require OpenTelemetry exporters.
    - exporter_not_installed: If True, skip tests that do not require OpenTelemetry exporters.
    """
    has_exporter = os.getenv("SDK_EXPORTER_INSTALLED", "").lower() in ("true", "1")
    if exporter_installed and has_exporter:
        return pytest.mark.skipif(True, reason="Tests not compatible with the opentelemetry exporters")
    elif exporter_not_installed and not has_exporter:
        return pytest.mark.skipif(True, reason="Tests only compatible with the opentelemetry exporters")
    return pytest.mark.skipif(False, reason="No skip condition met for OpenTelemetry logs exporter tests")


@skipif(exporter_installed=True)
def test_otel_sdk_not_installed_by_default():
    """
    Test that the OpenTelemetry logs exporter can be set up correctly.
    """
    from ddtrace.internal.opentelemetry.logs import set_otel_logs_exporter

    # This should not raise an ImportError
    set_otel_logs_exporter()

    # If the OpenTelemetry SDK is not installed
    with pytest.raises(ImportError):
        from opentelemetry.sdk.resources import Resource  # noqa: F401


@skipif(exporter_not_installed=True)
@pytest.mark.subprocess()
def test_otel_logs_exporter_installed():
    """
    Test that the OpenTelemetry logs exporter can be set up correctly.
    """
    from ddtrace.internal.opentelemetry.logs import set_otel_logs_exporter

    # This should not raise an ImportError
    set_otel_logs_exporter()

    # Check if the GRPC/protobuf exporter is available
    try:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

        assert OTLPLogExporter() is not None
    except ImportError:
        pytest.fail("OTLPLogExporter should be available")

    # Check if HTTP/protobuf exporter is available
    try:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        assert OTLPLogExporter() is not None
    except ImportError:
        pytest.fail("OTLPLogExporter for HTTP/protobuf should be available")


@skipif(exporter_not_installed=True)
@pytest.mark.subprocess(ddtrace_run=True, env={"DD_OTEL_LOGS_ENABLED": "true"})
def test_otel_logs_enabled():
    """
    Test that the OpenTelemetry logs exporter is automatically configured when DD_OTEL_LOGS_ENABLED is set.
    """
    from opentelemetry._logs import get_logger_provider

    from ddtrace.internal.opentelemetry.logs import LOGS_PROVIDER_CONFIGURED

    lp = get_logger_provider()
    assert LOGS_PROVIDER_CONFIGURED, f"OpenTelemetry logs exporter should be configured automatically. {lp} configured."


@skipif(exporter_not_installed=True)
@pytest.mark.subprocess(ddtrace_run=True, parametrize={"DD_OTEL_LOGS_ENABLED": [None, "false"]})
def test_otel_logs_disabled_and_unset():
    """
    Test that the OpenTelemetry logs exporter is NOT automatically configured when DD_OTEL_LOGS_ENABLED is set.
    """
    from opentelemetry._logs import get_logger_provider

    from ddtrace.internal.opentelemetry.logs import LOGS_PROVIDER_CONFIGURED

    lp = get_logger_provider()
    assert (
        not LOGS_PROVIDER_CONFIGURED
    ), f"OpenTelemetry logs exporter should not be configured automatically. {lp} configured."


@skipif(exporter_not_installed=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_OTEL_LOGS_ENABLED": "true",
        "DD_SERVICE": "ddservice",
        "DD_VERSION": "ddv1",
        "DD_ENV": "ddenv",
        "DD_HOSTNAME": "ddhost",
    },
    parametrize={"OTEL_EXPORTER_OTLP_PROTOCOL": ["http/protobuf"]},
)
def test_otel_logs_provider_auto_configured_http():
    """
    Test that the OpenTelemetry logs exporter is automatically configured for HTTP when DD_OTEL_LOGS_ENABLED is set.
    """
    from logging import getLogger
    import time
    from unittest.mock import Mock
    from unittest.mock import patch
    from urllib.parse import urlparse

    from opentelemetry._logs import get_logger_provider

    from ddtrace.settings._agent import config as agent_config
    from tests.opentelemetry.test_logs import decode_logs_request
    from tests.opentelemetry.test_logs import find_log_correlation_attributes

    log = getLogger()
    log.warning("hi")
    with patch("requests.sessions.Session.request") as mock_request:
        mock_response = Mock(status_code=200)
        mock_request.return_value = mock_response

        log.error("test_otel_logs_provider_auto_configured_http")

        lp = get_logger_provider()
        time.sleep(1)
        lp.force_flush()
        time.sleep(1)

        request_body = None
        for call in mock_request.call_args_list:
            method, url = call[0][:2]
            if method == "POST" and url == f"http://{urlparse(agent_config.trace_agent_url).hostname}:4318/v1/logs":
                request_body = call[1].get("data", None)
                break
        assert request_body is not None, "Expected a request body to be present in the OpenTelemetry "
        "logs exporter request {mock_request.call_args_list}"

    captured_logs = decode_logs_request(request_body)
    assert len(captured_logs.resource_logs) > 0, "Expected at least one resource log in the OpenTelemetry logs request"

    lc_attributes = find_log_correlation_attributes(captured_logs, "test_otel_logs_provider_auto_configured_http")
    assert len(lc_attributes) == 6, f"Expected 6 log correlation attributes but found: {lc_attributes}"
    assert (
        lc_attributes["service"] == "ddservice"
    ), f"Expected service.name to be 'ddservice' but found: {lc_attributes['service']}"
    assert (
        lc_attributes["env"] == "ddenv"
    ), f"Expected deployment.environment to be 'ddenv' but found: {lc_attributes['env']}"
    assert (
        lc_attributes["version"] == "ddv1"
    ), f"Expected service.version to be 'ddv1' but found: {lc_attributes['version']}"
    assert (
        lc_attributes["host_name"] == "ddhost"
    ), f"Expected host.name to be 'ddhost' but found: {lc_attributes['host_name']}"
    assert lc_attributes["trace_id"] in (
        "00000000000000000000000000000000",
        "",
    ), f"Expected trace_id to be '00000000000000000000000000000000' but found: {lc_attributes['trace_id']}"
    assert lc_attributes["span_id"] in (
        "0000000000000000",
        "",
    ), f"Expected span_id to be '0000000000000000' but found: {lc_attributes['span_id']}"


@skipif(exporter_not_installed=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={"DD_OTEL_LOGS_ENABLED": "true"},
    parametrize={"OTEL_EXPORTER_OTLP_PROTOCOL": ["grpc", None]},
)
def test_otel_logs_provider_auto_configured_grpc():
    """
    Test that OpenTelemetry logs exporter sends data via gRPC to a mocked OTLP endpoint.
    """
    from logging import getLogger
    import time

    from opentelemetry._logs import get_logger_provider

    from tests.opentelemetry.test_logs import mock_grpc_exporter_connection

    mock_service, server = mock_grpc_exporter_connection()
    try:
        server.start()
        logger = getLogger()
        logger.error("test_otel_logs_provider_auto_configured_grpc")

        lp = get_logger_provider()
        time.sleep(1)
        lp.force_flush()
        time.sleep(1)
    finally:
        server.stop(0)

    # Inspect captured requests
    assert mock_service.received_requests, "No gRPC Export requests were received by the mock server"

    # Flatten all log records
    all_logs = [
        record
        for request in mock_service.received_requests
        for resource_logs in request.resource_logs
        for scope_logs in resource_logs.scope_logs
        for record in scope_logs.log_records
    ]

    assert any(
        b"test_otel_logs_provider_auto_configured_grpc" in log.body.string_value.encode() for log in all_logs
    ), "Expected log message not found in exported gRPC payload"


@skipif(exporter_not_installed=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_OTEL_LOGS_ENABLED": "true",
        "DD_TRACE_OTEL_ENABLED": None,
        "DD_SERVICE": "test_service",
        "DD_VERSION": "1.0",
        "DD_ENV": "test_env",
        "DD_HOSTNAME": "test_host",
        "OTEL_RESOURCE_ATTRIBUTES": "service.name=test_service2,service.version=2.0,"
        "deployment.environment=test_env2,host.name=test_host2",
    },
)
def test_ddtrace_log_correlation():
    """
    Test that OpenTelemetry logs exporter supports correlating ddtrace traces with OpenTelemetry logs.
    """
    from logging import getLogger
    import os
    import time

    from opentelemetry._logs import get_logger_provider

    from ddtrace import tracer
    from tests.opentelemetry.test_logs import find_log_correlation_attributes
    from tests.opentelemetry.test_logs import mock_grpc_exporter_connection

    otel_context = os.environ.get("OTEL_PYTHON_CONTEXT")
    assert (
        otel_context == "ddcontextvars_context"
    ), f"Expected OTEL_PYTHON_CONTEXT to be set to ddcontextvars_context but found: {otel_context}"

    log = getLogger()

    mock_service, server = mock_grpc_exporter_connection()
    try:
        server.start()
        with tracer.trace("test_trace") as span:
            log.error("test_ddtrace_log_correlation")
        time.sleep(2)
        lp = get_logger_provider()
        lp.force_flush()
        time.sleep(2)
    finally:
        server.stop(0)

    lc_attributes = {}
    for request in mock_service.received_requests:
        lc_attributes = find_log_correlation_attributes(request, "test_ddtrace_log_correlation")
        if lc_attributes:
            break

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
    assert (
        int(lc_attributes["trace_id"], 16) == span.trace_id
    ), f"Expected trace_id_hex to be set to {lc_attributes['trace_id']} but found: {span.trace_id}"
    assert (
        int(lc_attributes["span_id"], 16) == span.span_id
    ), f"Expected span_id_hex to be set to {lc_attributes['span_id']} but found: {span.span_id}"


@skipif(exporter_not_installed=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_OTEL_LOGS_ENABLED": "true",
        "DD_TRACE_OTEL_ENABLED": "true",
        "OTEL_RESOURCE_ATTRIBUTES": "service.name=test_service,service.version=1.0,"
        "deployment.environment=test_env,host.name=test_host",
    },
)
def test_otel_trace_log_correlation():
    """
    Test that OpenTelemetry logs exporter supports correlating OpenTelemetry traces with OpenTelemetry logs.
    """
    from logging import getLogger
    import os
    import time

    from opentelemetry import trace
    from opentelemetry._logs import get_logger_provider

    from tests.opentelemetry.test_logs import find_log_correlation_attributes
    from tests.opentelemetry.test_logs import mock_grpc_exporter_connection

    otel_context = os.environ.get("OTEL_PYTHON_CONTEXT")
    assert (
        otel_context == "ddcontextvars_context"
    ), f"Expected OTEL_PYTHON_CONTEXT to be set to ddcontextvars_context but found: {otel_context}"

    log = getLogger()

    mock_service, server = mock_grpc_exporter_connection()
    try:
        server.start()
        oteltracer = trace.get_tracer(__name__)
        with oteltracer.start_as_current_span("test-otel-distributed-trace") as ot_span:
            log.error("test_otel_trace_log_correlation")
        time.sleep(2)
        lp = get_logger_provider()
        lp.force_flush()
        time.sleep(2)
    finally:
        server.stop(0)

    lc_attributes = {}
    for request in mock_service.received_requests:
        lc_attributes = find_log_correlation_attributes(request, "test_otel_trace_log_correlation")
        if lc_attributes:
            break

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
