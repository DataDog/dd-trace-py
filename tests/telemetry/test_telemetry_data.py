import platform
import sys

from ddtrace.internal.telemetry.data.application import APPLICATION
from ddtrace.internal.telemetry.data.application import Application
from ddtrace.internal.telemetry.data.application import get_version
from ddtrace.internal.telemetry.data.dependency import Dependency
from ddtrace.internal.telemetry.data.dependency import create_dependency
from ddtrace.internal.telemetry.data.host import HOST
from ddtrace.internal.telemetry.data.host import Host
from ddtrace.internal.telemetry.data.host import get_containter_id
from ddtrace.internal.telemetry.data.host import get_hostname
from ddtrace.internal.telemetry.data.host import get_os_version
from ddtrace.internal.telemetry.data.integration import Integration
from ddtrace.internal.telemetry.data.integration import create_integration
from ddtrace.internal.telemetry.data.metrics import Series
from ddtrace.internal.telemetry.data.payload import AppClosedPayload
from ddtrace.internal.telemetry.data.payload import AppGenerateMetricsPayload
from ddtrace.internal.telemetry.data.payload import AppIntegrationsChangedPayload
from ddtrace.internal.telemetry.data.payload import AppStartedPayload
from ddtrace.internal.telemetry.data.telemetry_request import create_telemetry_request
from ddtrace.internal.telemetry.data.telemetry_request import get_runtime_id


def test_create_dependency():
    name = "dependency_name"
    version = "0.0.0"
    dependency = create_dependency(name, version)

    assert dependency == {
        "name": name,
        "version": version,
    }  # type: Dependency


def test_create_integration():
    integration = create_integration("integration_name", "0.0.0", False, False, "no", "error")

    assert integration == {
        "name": "integration_name",
        "version": "0.0.0",
        "enabled": False,
        "auto_enabled": False,
        "compatible": "no",
        "error": "error",
    }  # type: Integration


def test_create_integration_with_default_args():
    name = "integration_name"
    integration = create_integration(name)

    assert integration == {
        "name": name,
        "version": "",
        "enabled": True,
        "auto_enabled": True,
        "compatible": "",
        "error": "",
    }  # type: Integration


def test_host_fields():
    expected_host = {
        "os": platform.platform(aliased=1, terse=1),
        "hostname": get_hostname(),
        "os_version": get_os_version(),
        "kernel_name": platform.system(),
        "kernel_release": platform.release(),
        "kernel_version": platform.version(),
        "container_id": get_containter_id(),
    }  # type: Host

    assert HOST == expected_host


def test_application_with_setenv(run_python_code_in_subprocess, monkeypatch):

    monkeypatch.setenv("DD_SERVICE", "test_service")
    monkeypatch.setenv("DD_VERSION", "12.34.56")
    monkeypatch.setenv("DD_ENV", "prod")

    out, err, status, _ = run_python_code_in_subprocess(
        """
from ddtrace.internal.telemetry.data.application import APPLICATION

assert APPLICATION["service_name"] == "test_service"
assert APPLICATION["service_version"] == "12.34.56"
assert APPLICATION["env"] == "prod"
"""
    )

    assert status == 0, (out, err)


def test_application():
    vi = sys.version_info
    language_version = "{}.{}.{}".format(vi.major, vi.minor, vi.micro)

    vi = sys.implementation.version
    runtime_version = "{}.{}.{}".format(vi.major, vi.minor, vi.micro)

    expected_application = {
        "service_name": "unnamed_python_service",
        "service_version": "",
        "env": "",
        "language_name": "python",
        "language_version": language_version,
        "tracer_version": get_version(),
        "runtime_name": sys.implementation.name,
        "runtime_version": runtime_version,
    }  # type: Application

    assert APPLICATION == expected_application


def test_create_telemetry_request():
    payload = AppClosedPayload()
    telmetry_request = create_telemetry_request(payload=payload)

    assert telmetry_request["body"]["tracer_time"] > 0
    telmetry_request["body"]["tracer_time"] = 0

    assert telmetry_request == {
        "headers": {
            "Content-type": "application/json",
            "DD-Telemetry-Request-Type": payload.request_type(),
            "DD-Telemetry-API-Version": "v1",
        },
        "body": {
            "tracer_time": 0,
            "runtime_id": get_runtime_id(),
            "api_version": "v1",
            "seq_id": 0,
            "application": APPLICATION,
            "host": HOST,
            "payload": payload.to_dict(),
            "request_type": payload.request_type(),
        },
    }


def test_app_started_payload():
    payload = AppStartedPayload()

    assert payload.request_type() == "app-started"
    assert len(payload.dependencies) > 0
    assert payload.to_dict() == {"dependencies": payload.dependencies}


def test_app_closed():
    payload = AppClosedPayload()

    assert payload.request_type() == "app-closed"
    assert payload.to_dict() == {}


def test_app_integrations_changed():
    integrations = [
        create_integration("integration-1"),
        create_integration("integration-2", enabled=False),
        create_integration("integration-3", error="error"),
    ]

    payload = AppIntegrationsChangedPayload(integrations)

    assert payload.request_type() == "app-integrations-changed"
    assert len(payload.integrations) == 3

    assert payload.to_dict() == {"integrations": integrations}


def test_generate_metrics():
    series_array = [Series("test.metric")]

    payload = AppGenerateMetricsPayload(series_array)

    assert payload.request_type() == "generate-metrics"
    assert len(payload.series) == 1

    assert payload.to_dict() == {
        "namespace": "tracers",
        "lib_language": "python",
        "lib_version": get_version(),
        "series": [s.to_dict() for s in series_array],
    }


def test_metrics_series():
    series = Series("test.metric")

    series.add_point(111111)
    series.add_point(222222)
    series.add_tag("foo", "bar")

    assert len(series.points) == 2
    assert len(series.points[0]) == 2
    assert len(series.points[1]) == 2

    assert series.points[0][1] == 111111
    assert series.points[1][1] == 222222

    assert series.to_dict() == {
        "metric": "test.metric",
        "points": series.points,
        "tags": {"foo": "bar"},
        "type": Series.COUNT,
        "common": False,
        "interval": None,
        "host": get_hostname(),
    }
