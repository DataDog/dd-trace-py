import mock

from ddtrace.internal.telemetry.data.application import APPLICATION
from ddtrace.internal.telemetry.data.dependency import Dependency
from ddtrace.internal.telemetry.data.dependency import create_dependency
from ddtrace.internal.telemetry.data.host import HOST
from ddtrace.internal.telemetry.data.integration import Integration
from ddtrace.internal.telemetry.data.integration import create_integration
from ddtrace.internal.telemetry.data.payload import AppClosedPayload
from ddtrace.internal.telemetry.data.telemetry_request import create_telemetry_request


def test_create_dependency():
    """tests create_dependency and validates return type"""
    name = "dependency_name"
    version = "0.0.0"
    dependency = create_dependency(name, version)

    assert dependency == {
        "name": name,
        "version": version,
    }  # type: Dependency


def test_create_integration():
    """tests create_integration and validates return type"""
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
    """validates the return value of create_integration when defualt arguments are used"""
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


def test_create_telemetry_request():
    """validates the return value of create_telemetry_request"""
    payload = AppClosedPayload()
    seq_id = 1

    with mock.patch("time.time") as t:
        t.return_value = 6543210
        with mock.patch("ddtrace.internal.telemetry.data.telemetry_request.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = create_telemetry_request(payload, seq_id)
            assert telmetry_request == {
                "headers": {
                    "Content-type": "application/json",
                    "DD-Telemetry-Request-Type": "app-closed",
                    "DD-Telemetry-API-Version": "v1",
                },
                "body": {
                    "tracer_time": 6543210,
                    "runtime_id": "1234-567",
                    "api_version": "v1",
                    "seq_id": seq_id,
                    "application": APPLICATION,
                    "host": HOST,
                    "payload": {},
                    "request_type": "app-closed",
                },
            }
