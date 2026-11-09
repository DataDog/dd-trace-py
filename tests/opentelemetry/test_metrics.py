import os

from opentelemetry import version
import pytest


OTEL_VERSION = tuple(int(x) for x in version.__version__.split(".")[:3])


def skipif(
    exporter_installed: bool = False, exporter_not_installed: bool = False, unsupported_otel_version: bool = False
):
    """
    Returns a pytest skip marker based on OpenTelemetry version and exporter installation.
    Parameters:
    - exporter_installed: If True, skip tests that require OpenTelemetry exporters.
    - exporter_not_installed: If True, skip tests that do not require OpenTelemetry exporters.
    - unsupported_otel_version: If True, skip tests that require OpenTelemetry version 1.12 or higher.
      - v1.12.0 is the first version that exposes metrics in the public API
    """
    if unsupported_otel_version and OTEL_VERSION < (1, 12):
        return pytest.mark.skipif(True, reason="OpenTelemetry version 1.12 or higher is required for these tests")

    has_exporter = os.getenv("SDK_EXPORTER_INSTALLED", "").lower() in ("true", "1")
    if exporter_installed and has_exporter:
        return pytest.mark.skipif(True, reason="Tests not compatible with the opentelemetry exporters")
    elif exporter_not_installed and not has_exporter:
        return pytest.mark.skipif(True, reason="Tests only compatible with the opentelemetry exporters")
    return pytest.mark.skipif(False, reason="No skip condition met for OpenTelemetry logs exporter tests")


@skipif(exporter_installed=True, unsupported_otel_version=True)
def test_otel_metrics_exporter_not_installed_by_default(caplog):
    """
    Test that the OpenTelemetry metrics exporter can be set up correctly.
    """
    from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

    # This should not raise an ImportError
    set_otel_meter_provider()

    assert (
        "Install opentelemetry-exporter-otlp, which includes both gRPC and HTTP exporters, before enabling "
        "OTLP metrics export."
    ) in caplog.text

    # The OpenTelemetry exporter dependencies are not installed.
    with pytest.raises(ImportError):
        from opentelemetry.sdk.resources import Resource  # noqa: F401


@skipif(exporter_installed=True)
def test_otel_metrics_exporter_import_warning(caplog):
    from ddtrace.internal.opentelemetry.metrics import _import_exporter

    assert _import_exporter("grpc") is None
    assert (
        "Install opentelemetry-exporter-otlp, which includes both gRPC and HTTP exporters, before enabling "
        "OTLP metrics export."
    ) in caplog.text


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess()
def test_otel_metrics_exporter_installed():
    """
    Test that the OpenTelemetry metrics exporter can be set up correctly.
    """
    from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

    # This should not raise an ImportError
    set_otel_meter_provider()

    # Check if the GRPC/protobuf exporter is available
    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

        assert OTLPMetricExporter() is not None
    except ImportError:
        pytest.fail("OTLPMetricExporter for gRPC protobuf should be available")

    # Check if HTTP/protobuf exporter is available
    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        assert OTLPMetricExporter() is not None
    except ImportError:
        pytest.fail("OTLPMetricExporter for HTTP/protobuf should be available")


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(ddtrace_run=True, env={"DD_METRICS_OTEL_ENABLED": "true"})
def test_otel_metrics_enabled():
    """
    Test that the OpenTelemetry metrics exporter is automatically configured when DD_METRICS_OTEL_ENABLED is set.
    """
    from opentelemetry.metrics import get_meter_provider

    meter_provider = get_meter_provider()
    assert meter_provider, "OpenTelemetry metrics exporter should be configured automatically."


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_SERVICE": "test_service",
        "DD_VERSION": "test_version",
        "DD_ENV": "test_env",
        "OTEL_RESOURCE_ATTRIBUTES": "service.name=tag_service,service.version=tag_version,"
        "deployment.environment.name=tag_env",
    },
)
def test_otel_metrics_resource_uses_canonical_semantic_attributes():
    from ddtrace.internal.opentelemetry.metrics import _build_resource

    resource = _build_resource()
    assert resource is not None
    assert resource.attributes["service.name"] == "test_service"
    assert resource.attributes["service.version"] == "test_version"
    assert resource.attributes["deployment.environment.name"] == "test_env"
    assert not {"service", "version", "env", "deployment.environment"} & resource.attributes.keys()


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "OTEL_RESOURCE_ATTRIBUTES": "service.name=tag_service,service.version=tag_version,"
        "deployment.environment.name=tag_env"
    },
)
def test_otel_metrics_resource_remaps_semantic_tags():
    from ddtrace import config
    from ddtrace.internal.opentelemetry.metrics import _build_resource

    assert config.service == "tag_service"
    assert config.version == "tag_version"
    assert config.env == "tag_env"
    assert config.tags == {"env": "tag_env"}
    resource = _build_resource()
    assert resource is not None
    assert resource.attributes["service.name"] == "tag_service"
    assert resource.attributes["service.version"] == "tag_version"
    assert resource.attributes["deployment.environment.name"] == "tag_env"
    assert not {"service", "version", "env", "deployment.environment"} & resource.attributes.keys()


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(ddtrace_run=True, parametrize={"DD_METRICS_OTEL_ENABLED": [None, "false"]})
def test_otel_metrics_disabled_and_unset():
    """
    Test that the OpenTelemetry metrics exporter is NOT automatically configured when DD_METRICS_OTEL_ENABLED is set.
    """
    from opentelemetry.metrics import get_meter_provider

    meter_provider = get_meter_provider()
    assert (meter_provider is None) or (type(meter_provider).__name__ == "_ProxyMeterProvider"), (
        "OpenTelemetry mterics exporter should not be configured automatically."
    )


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_METRICS_OTEL_ENABLED": "true",
        "DD_TRACE_WRITER_INTERVAL_SECONDS": "30",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4317",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:14318/v1/traces",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
        "OTEL_TRACES_EXPORTER": "otlp",
    },
    parametrize={"OTEL_EXPORTER_OTLP_PROTOCOL": ["grpc", "http/protobuf"]},
)
def test_otel_metrics_export_does_not_generate_client_spans():
    import os

    from opentelemetry.metrics import get_meter
    from opentelemetry.metrics import get_meter_provider

    from ddtrace.trace import tracer
    from tests.opentelemetry.utils import capture_otlp_metrics
    from tests.opentelemetry.utils import capture_otlp_traces
    from tests.opentelemetry.utils import metric_names

    with (
        capture_otlp_metrics(os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"]) as metric_requests,
        capture_otlp_traces("http/protobuf", port=14318) as trace_requests,
    ):
        get_meter("test.metrics").create_counter("test.metric").add(1)
        assert get_meter_provider().force_flush()
        tracer.flush()

    assert "test.metric" in metric_names(metric_requests)
    assert trace_requests == []


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_METRICS_OTEL_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://127.0.0.1:14319/v1/metrics",
    },
    parametrize={"OTEL_EXPORTER_OTLP_PROTOCOL": [None, "", "http/protobuf"]},
)
def test_otel_metrics_export_defaults_to_http_protobuf():
    from opentelemetry.metrics import get_meter
    from opentelemetry.metrics import get_meter_provider
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

    from tests.opentelemetry.utils import _capture_http_requests
    from tests.opentelemetry.utils import metric_names

    with _capture_http_requests(14319, ExportMetricsServiceRequest) as requests:
        get_meter("test.metrics").create_counter("test.default.protocol").add(1)
        assert get_meter_provider().force_flush()

    assert "test.default.protocol" in metric_names(requests)


OTLP_EXPORT_PROBE = "otlp.configuration.probe"


def _export_signal_code(signal):
    if signal == "logs":
        return """
from logging import getLogger
from opentelemetry._logs import get_logger_provider
getLogger("configuration-test").error("otlp.configuration.probe")
assert get_logger_provider().force_flush()
"""
    return """
from opentelemetry.metrics import get_meter, get_meter_provider
get_meter("configuration-test").create_counter("otlp.configuration.probe").add(1)
assert get_meter_provider().force_flush()
"""


def _otlp_test_env(signal, protocol):
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("OTEL_EXPORTER_OTLP_"):
            env.pop(key)
    env.update(
        {
            "DD_LOGS_OTEL_ENABLED": str(signal == "logs"),
            "DD_METRICS_OTEL_ENABLED": str(signal == "metrics"),
            "DD_TRACE_ENABLED": "false",
            "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
            "OTEL_EXPORTER_OTLP_ENDPOINT": (
                "http://127.0.0.1:14317" if protocol == "grpc" else "http://127.0.0.1:14319"
            ),
        }
    )
    return env


def _assert_exported_probe(requests, signal, protocol, compression="none"):
    import gzip
    import zlib

    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

    from tests.opentelemetry.utils import metric_names

    assert requests, "Expected an OTLP export request"
    decoded = []
    for request in requests:
        if protocol == "http/protobuf":
            path, headers, body = request
            headers = {key.lower(): value for key, value in headers.items()}
            assert path == "/v1/" + signal
            assert headers["content-type"] == "application/x-protobuf"
            assert headers.get("content-encoding", "none") == compression
            if compression == "gzip":
                body = gzip.decompress(body)
            elif compression == "deflate":
                body = zlib.decompress(body)
            request = ExportLogsServiceRequest() if signal == "logs" else ExportMetricsServiceRequest()
            request.ParseFromString(body)
        decoded.append(request)
    if signal == "logs":
        assert any(
            record.body.string_value == OTLP_EXPORT_PROBE
            for request in decoded
            for resource in request.resource_logs
            for scope in resource.scope_logs
            for record in scope.log_records
        )
    else:
        assert OTLP_EXPORT_PROBE in metric_names(decoded)


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.parametrize("signal", ["logs", "metrics"])
@pytest.mark.parametrize(
    "global_protocol,signal_protocol,expected_protocol,invalid_value",
    [
        ("HTTP/PROTOBUF", None, "http/protobuf", None),
        ("GRPC", None, "grpc", None),
        (" HTTP/PROTOBUF ", None, "http/protobuf", None),
        ("grpc", " GRPC ", "grpc", None),
        ("unsupported", None, "http/protobuf", "unsupported"),
        ("http/json", None, "http/protobuf", "http/json"),
        ("grpc", "HTTP/PROTOBUF", "http/protobuf", None),
        ("http/protobuf", "GRPC", "grpc", None),
        ("grpc", "unsupported", "http/protobuf", "unsupported"),
        ("grpc", "http/json", "http/protobuf", "http/json"),
        ("unsupported", "grpc", "grpc", None),
        ("grpc", "", "grpc", None),
    ],
)
def test_otlp_protocol_parsing_exports(
    signal, global_protocol, signal_protocol, expected_protocol, invalid_value, ddtrace_run_python_code_in_subprocess
):
    from tests.opentelemetry.utils import capture_otlp_signal

    env = _otlp_test_env(signal, expected_protocol)
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = global_protocol
    if signal_protocol is not None:
        env["OTEL_EXPORTER_OTLP_" + signal.upper() + "_PROTOCOL"] = signal_protocol
    with capture_otlp_signal(signal, expected_protocol) as requests:
        stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=_export_signal_code(signal), env=env)
    assert status == 0, (stdout, stderr)
    assert requests, (stdout, stderr)
    _assert_exported_probe(requests, signal, expected_protocol)
    if invalid_value is not None:
        diagnostic = stderr.decode().lower()
        assert invalid_value in diagnostic, diagnostic
        assert "http/protobuf" in diagnostic, diagnostic
        assert "unsupported" in diagnostic or "not supported" in diagnostic or "invalid" in diagnostic, diagnostic
    elif global_protocol == "unsupported":
        assert b"unsupported" not in stderr, stderr


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.parametrize("signal", ["logs", "metrics"])
@pytest.mark.parametrize("protocol", ["http/protobuf", "grpc"])
@pytest.mark.parametrize(
    "global_compression,signal_compression,expected_compression,invalid",
    [
        ("not-a-compression", None, "none", True),
        ("gzip", "not-a-compression", "none", True),
        ("not-a-compression", "gzip", "gzip", False),
        ("gzip", "none", "none", False),
        ("none", "gzip", "gzip", False),
        ("gzip", "", "gzip", False),
    ],
)
def test_otlp_compression_parsing_exports(
    signal,
    protocol,
    global_compression,
    signal_compression,
    expected_compression,
    invalid,
    ddtrace_run_python_code_in_subprocess,
):
    from tests.opentelemetry.utils import capture_otlp_signal

    env = _otlp_test_env(signal, protocol)
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = protocol
    env["OTEL_EXPORTER_OTLP_COMPRESSION"] = global_compression
    if signal_compression is not None:
        env["OTEL_EXPORTER_OTLP_" + signal.upper() + "_COMPRESSION"] = signal_compression
    with capture_otlp_signal(signal, protocol) as requests:
        stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=_export_signal_code(signal), env=env)
    assert status == 0, (stdout, stderr)
    assert requests, (stdout, stderr)
    _assert_exported_probe(requests, signal, protocol, expected_compression)
    if invalid:
        diagnostic = stderr.decode().lower()
        assert "not-a-compression" in diagnostic, diagnostic
        assert "unsupported" in diagnostic or "not supported" in diagnostic or "invalid" in diagnostic, diagnostic
    elif global_compression == "not-a-compression":
        assert b"not-a-compression" not in stderr, stderr


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.parametrize("signal", ["logs", "metrics"])
def test_otlp_http_deflate_compression_is_preserved(signal, ddtrace_run_python_code_in_subprocess):
    from tests.opentelemetry.utils import capture_otlp_signal

    env = _otlp_test_env(signal, "http/protobuf")
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
    env["OTEL_EXPORTER_OTLP_COMPRESSION"] = "deflate"
    with capture_otlp_signal(signal, "http/protobuf") as requests:
        stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code=_export_signal_code(signal), env=env)
    assert status == 0, (stdout, stderr)
    _assert_exported_probe(requests, signal, "http/protobuf", "deflate")


@skipif(exporter_not_installed=True, unsupported_otel_version=True)
@pytest.mark.parametrize("signal", ["logs", "metrics"])
@pytest.mark.parametrize("signal_override", [False, True])
def test_otlp_invalid_compression_configures_uncompressed_grpc_channel(
    signal, signal_override, run_python_code_in_subprocess
):
    env = _otlp_test_env(signal, "grpc")
    env["DD_LOGS_OTEL_ENABLED"] = "false"
    env["DD_METRICS_OTEL_ENABLED"] = "false"
    env["OTEL_EXPORTER_OTLP_COMPRESSION"] = "gzip" if signal_override else "not-a-compression"
    if signal_override:
        env["OTEL_EXPORTER_OTLP_" + signal.upper() + "_COMPRESSION"] = "not-a-compression"
    code = f"""
from unittest.mock import patch
import grpc
from opentelemetry.exporter.otlp.proto.grpc import exporter as grpc_exporter
from ddtrace.internal.opentelemetry.{signal} import _import_exporter

with patch.object(grpc_exporter, "insecure_channel", wraps=grpc_exporter.insecure_channel) as create_channel:
    exporter = _import_exporter("grpc")()
    try:
        assert create_channel.call_args.kwargs["compression"] == grpc.Compression.NoCompression
    finally:
        exporter.shutdown()
"""
    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert b"not-a-compression" in stderr, stderr
