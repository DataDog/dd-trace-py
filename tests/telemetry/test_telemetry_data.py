from ddtrace.internal.telemetry.data.application import APPLICATION
from ddtrace.internal.telemetry.data.dependency import Dependency
from ddtrace.internal.telemetry.data.dependency import create_dependency
from ddtrace.internal.telemetry.data.host import HOST
from ddtrace.internal.telemetry.data.integration import Integration
from ddtrace.internal.telemetry.data.integration import create_integration
from ddtrace.internal.telemetry.data.payload import AppClosedPayload
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


def test_create_telemetry_request():
    payload = AppClosedPayload()
    telmetry_request = create_telemetry_request(payload=payload)

    # tracer_time is set using time.time, assert the value set is valid
    assert telmetry_request["body"]["tracer_time"] > 0
    # ignore the tracer_time field in the assertion below, this value might differ
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
