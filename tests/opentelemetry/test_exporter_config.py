from contextlib import contextmanager
import os

from opentelemetry import version
import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("SDK_EXPORTER_INSTALLED", "").lower() not in ("true", "1")
    or tuple(int(x) for x in version.__version__.split(".")[:3]) < (1, 15, 0),
    reason="OpenTelemetry exporters and API version 1.15 or higher are required",
)


OTLP_EXPORT_PROBE = "otlp.configuration.probe"


def _export_signal(signal):
    if signal == "logs":
        from logging import getLogger

        from opentelemetry._logs import get_logger_provider

        getLogger("configuration-test").error(OTLP_EXPORT_PROBE)
        assert get_logger_provider().force_flush()
    else:
        from opentelemetry.metrics import get_meter
        from opentelemetry.metrics import get_meter_provider

        get_meter("configuration-test").create_counter(OTLP_EXPORT_PROBE).add(1)
        assert get_meter_provider().force_flush()


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
            "DD_TRACE_LOGGING_RATE": "0",
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
        stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(
            code=f"from tests.opentelemetry.test_exporter_config import _export_signal\n_export_signal({signal!r})",
            env=env,
        )
    assert status == 0, (stdout, stderr)
    assert requests, (stdout, stderr)
    _assert_exported_probe(requests, signal, expected_protocol)
    if invalid_value is not None:
        diagnostic = stderr.decode().lower()
        assert invalid_value in diagnostic, diagnostic
        assert "http/protobuf" in diagnostic, diagnostic
        assert "unsupported" in diagnostic or "not supported" in diagnostic or "invalid" in diagnostic, diagnostic
    elif global_protocol == "unsupported":
        diagnostic = stderr.decode().lower()
        other_signal = "metrics" if signal == "logs" else "logs"
        assert "opentelemetry " + signal + " exporter protocol" not in diagnostic, diagnostic
        assert "opentelemetry " + other_signal + " exporter protocol" in diagnostic, diagnostic
        # Both ddtrace-run and the target process construct their own configuration.
        assert diagnostic.count("unsupported") == 2, diagnostic


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
        stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(
            code=f"from tests.opentelemetry.test_exporter_config import _export_signal\n_export_signal({signal!r})",
            env=env,
        )
    assert status == 0, (stdout, stderr)
    assert requests, (stdout, stderr)
    _assert_exported_probe(requests, signal, protocol, expected_compression)
    if invalid:
        diagnostic = stderr.decode().lower()
        assert "not-a-compression" in diagnostic, diagnostic
        assert "unsupported" in diagnostic or "not supported" in diagnostic or "invalid" in diagnostic, diagnostic
    elif global_compression == "not-a-compression":
        diagnostic = stderr.decode().lower()
        # The other signal warns once in ddtrace-run and once in the target process.
        assert diagnostic.count("not-a-compression") == 2, diagnostic
        assert "for otel_exporter_otlp_compression" in diagnostic, diagnostic


@pytest.mark.parametrize("signal", ["logs", "metrics"])
def test_otlp_http_deflate_compression_is_preserved(signal, ddtrace_run_python_code_in_subprocess):
    from tests.opentelemetry.utils import capture_otlp_signal

    env = _otlp_test_env(signal, "http/protobuf")
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
    env["OTEL_EXPORTER_OTLP_COMPRESSION"] = "deflate"
    with capture_otlp_signal(signal, "http/protobuf") as requests:
        stdout, stderr, status, _ = ddtrace_run_python_code_in_subprocess(
            code=f"from tests.opentelemetry.test_exporter_config import _export_signal\n_export_signal({signal!r})",
            env=env,
        )
    assert status == 0, (stdout, stderr)
    _assert_exported_probe(requests, signal, "http/protobuf", "deflate")


@pytest.mark.parametrize("signal", ["logs", "metrics"])
@pytest.mark.parametrize("signal_override", [False, True])
def test_otlp_invalid_compression_configures_uncompressed_grpc_channel(
    signal, signal_override, run_python_code_in_subprocess
):
    env = _otlp_test_env(signal, "grpc")
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "grpc"
    env["DD_LOGS_OTEL_ENABLED"] = "false"
    env["DD_METRICS_OTEL_ENABLED"] = "false"
    env["OTEL_EXPORTER_OTLP_COMPRESSION"] = "gzip" if signal_override else "not-a-compression"
    if signal_override:
        env["OTEL_EXPORTER_OTLP_" + signal.upper() + "_COMPRESSION"] = "not-a-compression"
    code = (
        "from tests.opentelemetry.test_exporter_config import _assert_uncompressed_grpc_channel\n"
        f"_assert_uncompressed_grpc_channel({signal!r})"
    )
    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert b"not-a-compression" in stderr, stderr


def _assert_uncompressed_grpc_channel(signal):
    from unittest.mock import patch

    import grpc
    from opentelemetry.exporter.otlp.proto.grpc import exporter as grpc_exporter

    if signal == "logs":
        from ddtrace.internal.opentelemetry.logs import _import_exporter
    else:
        from ddtrace.internal.opentelemetry.metrics import _import_exporter

    with patch.object(grpc_exporter, "insecure_channel", wraps=grpc_exporter.insecure_channel) as create_channel:
        exporter = _import_exporter("grpc")()
        try:
            assert create_channel.call_args.kwargs["compression"] == grpc.Compression.NoCompression
        finally:
            exporter.shutdown()


def _assert_cached_protocol_diagnostics(signal, replacement, expected_protocol, invalid_value):
    with _capture_configuration_warnings("protocol") as warnings:
        from ddtrace.internal.settings._opentelemetry import otel_config

        assert getattr(otel_config.exporter, signal.upper() + "_PROTOCOL") == expected_protocol
        if invalid_value is None:
            assert not warnings, warnings
        else:
            assert len(warnings) == 1, warnings
            assert invalid_value in warnings[0], warnings
            assert expected_protocol in warnings[0], warnings
        initial_warnings = list(warnings)
        os.environ["OTEL_EXPORTER_OTLP_" + signal.upper() + "_PROTOCOL"] = replacement
        if signal == "logs":
            from ddtrace.internal.opentelemetry.logs import set_otel_logs_provider

            set_otel_logs_provider()
        else:
            from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

            set_otel_meter_provider()
        _export_signal(signal)
        assert getattr(otel_config.exporter, signal.upper() + "_PROTOCOL") == expected_protocol
        assert warnings == initial_warnings, warnings


@pytest.mark.parametrize("signal", ["logs", "metrics"])
@pytest.mark.parametrize(
    "configured,replacement,expected_protocol,invalid_value",
    [
        ("grpc", "changed-to-invalid", "grpc", None),
        ("cached-invalid", "grpc", "http/protobuf", "cached-invalid"),
    ],
)
def test_otlp_protocol_diagnostics_use_configured_snapshot(
    signal, configured, replacement, expected_protocol, invalid_value, run_python_code_in_subprocess
):
    from tests.opentelemetry.utils import capture_otlp_signal

    env = _otlp_test_env(signal, expected_protocol)
    env["DD_LOGS_OTEL_ENABLED"] = "false"
    env["DD_METRICS_OTEL_ENABLED"] = "false"
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "grpc"
    env["OTEL_EXPORTER_OTLP_" + signal.upper() + "_PROTOCOL"] = configured
    code = (
        "from tests.opentelemetry.test_exporter_config import _assert_cached_protocol_diagnostics\n"
        f"_assert_cached_protocol_diagnostics({signal!r}, {replacement!r}, {expected_protocol!r}, {invalid_value!r})"
    )
    with capture_otlp_signal(signal, expected_protocol) as requests:
        stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    _assert_exported_probe(requests, signal, expected_protocol)


@contextmanager
def _capture_configuration_warnings(keyword):
    import logging

    warnings = []

    class WarningHandler(logging.Handler):
        def emit(self, record):
            if record.levelno == logging.WARNING and keyword in record.getMessage().lower():
                warnings.append(record.getMessage())

    handler = WarningHandler()
    logger = logging.getLogger("ddtrace")
    logger.addHandler(handler)
    try:
        yield warnings
    finally:
        logger.removeHandler(handler)


def _assert_cached_compression_diagnostics(signal, variable, replacement, invalid_value):
    with _capture_configuration_warnings("compression") as warnings:
        from ddtrace.internal.settings._opentelemetry import otel_config

        if invalid_value is None:
            assert not warnings, warnings
        else:
            expected_count = 2 if variable == "OTEL_EXPORTER_OTLP_COMPRESSION" else 1
            assert len(warnings) == expected_count, warnings
            assert all(invalid_value in message and variable in message for message in warnings), warnings
            assert all("no compression" in message.lower() for message in warnings), warnings
        initial_warnings = list(warnings)
        expected_compression = "none" if invalid_value else "gzip"
        assert getattr(otel_config.exporter, signal.upper() + "_COMPRESSION") == expected_compression
        os.environ[variable] = replacement
        if signal == "logs":
            from ddtrace.internal.opentelemetry.logs import set_otel_logs_provider

            set_otel_logs_provider()
        else:
            from ddtrace.internal.opentelemetry.metrics import set_otel_meter_provider

            set_otel_meter_provider()
        _export_signal(signal)
        assert getattr(otel_config.exporter, signal.upper() + "_COMPRESSION") == expected_compression
        assert warnings == initial_warnings, warnings


@pytest.mark.parametrize("signal", ["logs", "metrics"])
@pytest.mark.parametrize("signal_override", [False, True])
@pytest.mark.parametrize(
    "configured,replacement,expected_compression,invalid_value",
    [
        ("gzip", "changed-to-invalid", "gzip", None),
        ("cached-invalid", "gzip", "none", "cached-invalid"),
    ],
)
def test_otlp_compression_diagnostics_use_configured_snapshot(
    signal,
    signal_override,
    configured,
    replacement,
    expected_compression,
    invalid_value,
    run_python_code_in_subprocess,
):
    from tests.opentelemetry.utils import capture_otlp_signal

    env = _otlp_test_env(signal, "http/protobuf")
    env["DD_LOGS_OTEL_ENABLED"] = "false"
    env["DD_METRICS_OTEL_ENABLED"] = "false"
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
    variable = "OTEL_EXPORTER_OTLP_COMPRESSION"
    if signal_override:
        env[variable] = "gzip"
        variable = "OTEL_EXPORTER_OTLP_" + signal.upper() + "_COMPRESSION"
    env[variable] = configured
    code = (
        "from tests.opentelemetry.test_exporter_config import _assert_cached_compression_diagnostics\n"
        f"_assert_cached_compression_diagnostics({signal!r}, {variable!r}, {replacement!r}, {invalid_value!r})"
    )
    with capture_otlp_signal(signal, "http/protobuf") as requests:
        stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    _assert_exported_probe(requests, signal, "http/protobuf", expected_compression)


def _assert_explicit_compression_override(signal, protocol):
    from unittest.mock import patch

    with _capture_configuration_warnings("compression") as warnings:
        if signal == "logs":
            from ddtrace.internal.opentelemetry.logs import _import_exporter
        else:
            from ddtrace.internal.opentelemetry.metrics import _import_exporter

        assert len(warnings) == 2, warnings
        assert any("invalid-global-compression" in message for message in warnings), warnings
        assert any("invalid-signal-compression" in message for message in warnings), warnings
        initial_warnings = list(warnings)
        if protocol == "grpc":
            from grpc import Compression
            from opentelemetry.exporter.otlp.proto.grpc import exporter as grpc_exporter

            channel_context = patch.object(grpc_exporter, "insecure_channel", wraps=grpc_exporter.insecure_channel)
        else:
            from contextlib import nullcontext

            from opentelemetry.exporter.otlp.proto.http import Compression

            channel_context = nullcontext()
        with channel_context as create_channel:
            exporter = _import_exporter(protocol)(compression=Compression.Gzip)
            try:
                if protocol == "grpc":
                    assert create_channel.call_args.kwargs["compression"] == Compression.Gzip
                else:
                    assert exporter._compression == Compression.Gzip
            finally:
                exporter.shutdown()
        assert warnings == initial_warnings, warnings


@pytest.mark.parametrize("signal", ["logs", "metrics"])
@pytest.mark.parametrize("protocol", ["http/protobuf", "grpc"])
def test_otlp_explicit_compression_override_preserves_constructor_value(
    signal, protocol, run_python_code_in_subprocess
):
    env = _otlp_test_env(signal, protocol)
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = protocol
    env["DD_LOGS_OTEL_ENABLED"] = "false"
    env["DD_METRICS_OTEL_ENABLED"] = "false"
    env["OTEL_EXPORTER_OTLP_COMPRESSION"] = "invalid-global-compression"
    env["OTEL_EXPORTER_OTLP_" + signal.upper() + "_COMPRESSION"] = "invalid-signal-compression"
    code = (
        "from tests.opentelemetry.test_exporter_config import _assert_explicit_compression_override\n"
        f"_assert_explicit_compression_override({signal!r}, {protocol!r})"
    )
    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert b"invalid-global-compression" in stderr, stderr
    assert b"invalid-signal-compression" in stderr, stderr
