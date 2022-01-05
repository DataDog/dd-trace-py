import mock

from ddtrace.internal.telemetry.data import APPLICATION
from ddtrace.internal.telemetry.data import HOST
from ddtrace.internal.telemetry.telemetry_request import _create_telemetry_request
from ddtrace.internal.telemetry.telemetry_request import app_closed_telemetry_request
from ddtrace.internal.telemetry.telemetry_request import app_integrations_changed_telemetry_request
from ddtrace.internal.telemetry.telemetry_request import app_started_telemetry_request


def test_create_telemetry_request():
    """validates the return value of _create_telemetry_request"""
    with mock.patch("ddtrace.internal.telemetry.telemetry_request.monotonic") as t:
        t.return_value = 888366600
        with mock.patch("ddtrace.internal.telemetry.telemetry_request.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = _create_telemetry_request(payload={}, payload_type="app-closed", seq_id=1)
            assert telmetry_request == {
                "headers": {
                    "Content-type": "application/json",
                    "DD-Telemetry-Request-Type": "app-closed",
                    "DD-Telemetry-API-Version": "v1",
                },
                "body": {
                    "tracer_time": 888366600,
                    "runtime_id": "1234-567",
                    "api_version": "v1",
                    "seq_id": 1,
                    "application": APPLICATION,
                    "host": HOST,
                    "payload": {},
                    "request_type": "app-closed",
                },
            }


def test_app_closed_telemetry_request():
    """validates the return value of _create_telemetry_request"""
    with mock.patch("time.time") as t:
        t.return_value = 888366600
        with mock.patch("ddtrace.internal.telemetry.telemetry_request.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = app_closed_telemetry_request(seq_id=2)

            assert telmetry_request["headers"]["DD-Telemetry-Request-Type"] == "app-closed"
            assert telmetry_request["body"]["request_type"] == "app-closed"
            assert telmetry_request["body"]["seq_id"] == 2
            assert telmetry_request["body"]["payload"] == {}


def test_app_integrations_changed_telemetry_request():
    """validates the return value of _create_telemetry_request"""

    integrations = [
        {
            "name": "integration-name",
            "version": "",
            "enabled": True,
            "auto_enabled": True,
            "compatible": "",
            "error": "",
        }
    ]

    with mock.patch("time.time") as t:
        t.return_value = 888366600
        with mock.patch("ddtrace.internal.telemetry.telemetry_request.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = app_integrations_changed_telemetry_request(integrations=integrations, seq_id=3)

            assert telmetry_request["headers"]["DD-Telemetry-Request-Type"] == "app-integrations-changed"
            assert telmetry_request["body"]["request_type"] == "app-integrations-changed"
            assert telmetry_request["body"]["seq_id"] == 3
            assert telmetry_request["body"]["payload"] == {
                "integrations": integrations,
            }


def test_app_started_telemetry_request():
    """validates the return value of _create_telemetry_request"""

    with mock.patch("time.time") as t:
        t.return_value = 888366600
        with mock.patch("ddtrace.internal.telemetry.telemetry_request.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = app_started_telemetry_request()

            assert telmetry_request["headers"]["DD-Telemetry-Request-Type"] == "app-started"
            assert telmetry_request["body"]["request_type"] == "app-started"
            assert telmetry_request["body"]["seq_id"] == 0
            assert len(telmetry_request["body"]["payload"]["dependencies"]) > 0
