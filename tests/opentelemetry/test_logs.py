import os

from opentelemetry import version
import pytest


OTEL_VERSION = tuple(int(x) for x in version.__version__.split(".")[:3])


def skipif(exporter_installed: bool = False, unsupported_otel_version: bool = False):
    if unsupported_otel_version and OTEL_VERSION < (1, 16):
        return pytest.mark.skipif(True, reason="OpenTelemetry version 1.16 or higher is required for these tests")

    is_exporter = os.getenv("OTEL_SDK_EXPORTER_INSTALLED", "").lower() in ("true", "1")
    if exporter_installed:
        return pytest.mark.skipif(is_exporter, reason="Tests not compatible with the opentelemetry exporters")
    else:
        return pytest.mark.skipif(not is_exporter, reason="Tests only compatible with the opentelemetry exporters")


@skipif(exporter_installed=True, unsupported_otel_version=True)
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


@skipif(unsupported_otel_version=True)
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


@skipif(unsupported_otel_version=True)
@pytest.mark.subprocess(ddtrace_run=True, env={"DD_OTEL_LOGS_ENABLED": "true"})
def test_otel_logs_provider_auto_configured():
    """
    Test that the OpenTelemetry logs exporter is automatically configured when DD_OTEL_LOGS_ENABLED is set.
    """
    from opentelemetry._logs import get_logger_provider

    from ddtrace.internal.opentelemetry.logs import LOGS_PROVIDER_CONFIGURED

    lp = get_logger_provider()
    assert LOGS_PROVIDER_CONFIGURED, f"OpenTelemetry logs exporter should be configured automatically. {lp} configured."


@skipif(unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={"DD_OTEL_LOGS_ENABLED": "true"},
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

    log = getLogger()
    log.error("hi")
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
            if method == "POST" and url == f"http://{urlparse(agent_config).hostname}:4318/v1/logs":
                request_body = call[1].get("data", None)
                break
        assert request_body is not None, "Expected a request body to be present in the OpenTelemetry "
        "logs exporter request {mock_request.call_args_list}"

    for expected in [
        b"service.name",
        b"service.version",
        b"deployment.environment",
        b"test_otel_logs_provider_auto_configured_http",
        b"hi",
    ]:
        assert expected in request_body, f"Expected {expected!r} in request body: {request_body}"


@skipif(unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={"DD_OTEL_LOGS_ENABLED": "true"},
    parametrize={"OTEL_EXPORTER_OTLP_PROTOCOL": ["grpc", None]},
)
def test_otel_logs_provider_auto_configured_grpc():
    """
    Test that OpenTelemetry logs exporter sends data via gRPC to a mocked OTLP endpoint.
    """
    from concurrent import futures
    from logging import getLogger
    import time
    from urllib.parse import urlparse

    import grpc
    from opentelemetry._logs import get_logger_provider
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2_grpc import LogsServiceServicer
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2_grpc import add_LogsServiceServicer_to_server

    from ddtrace.settings._agent import config as agent_config

    class MockLogsService(LogsServiceServicer):
        def __init__(self):
            self.received_requests = []

        def Export(self, request, context):
            self.received_requests.append(request)
            return ExportLogsServiceResponse()

    mock_service = MockLogsService()

    # Start gRPC server in background thread
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    add_LogsServiceServicer_to_server(mock_service, server)
    _ = server.add_insecure_port(f"http://{urlparse(agent_config).hostname}:4317")
    server.start()

    try:
        logger = getLogger()
        logger.error("test_otel_logs_provider_auto_configured_grpc")

        lp = get_logger_provider()
        time.sleep(2)
        lp.force_flush()
        time.sleep(2)

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
