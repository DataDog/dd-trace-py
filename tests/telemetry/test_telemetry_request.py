import mock

from ddtrace.internal.telemetry.data import get_application
from ddtrace.internal.telemetry.data import get_host
from ddtrace.internal.telemetry.telemetry_request import _create_telemetry_request
from ddtrace.internal.telemetry.telemetry_request import app_closed_telemetry_request
from ddtrace.internal.telemetry.telemetry_request import app_integrations_changed_telemetry_request
from ddtrace.internal.telemetry.telemetry_request import app_started_telemetry_request
from ddtrace.settings import _config as config


def test_create_telemetry_request():
    """validates the return value of _create_telemetry_request"""
    with mock.patch("ddtrace.internal.telemetry.telemetry_request.monotonic") as t:
        t.return_value = 888366600
        with mock.patch("ddtrace.internal.telemetry.telemetry_request.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = _create_telemetry_request(payload={}, payload_type="app-closed")
            assert telmetry_request == {
                "tracer_time": 888366600,
                "runtime_id": "1234-567",
                "api_version": "v1",
                "seq_id": -1,
                "application": get_application(config.service, config.version, config.env),
                "host": get_host(),
                "payload": {},
                "request_type": "app-closed",
            }


def test_app_closed_telemetry_request():
    """validates the return value of _create_telemetry_request"""
    with mock.patch("time.time") as t:
        t.return_value = 888366600
        with mock.patch("ddtrace.internal.telemetry.telemetry_request.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = app_closed_telemetry_request()

            assert telmetry_request["request_type"] == "app-closed"
            assert telmetry_request["seq_id"] == -1
            assert telmetry_request["payload"] == {}


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

            telmetry_request = app_integrations_changed_telemetry_request(integrations)

            assert telmetry_request["request_type"] == "app-integrations-changed"
            assert telmetry_request["seq_id"] == -1
            assert telmetry_request["payload"] == {
                "integrations": integrations,
            }


def test_app_started_telemetry_request():
    """validates the return value of _create_telemetry_request"""

    with mock.patch("time.time") as t:
        t.return_value = 888366600
        with mock.patch("ddtrace.internal.telemetry.telemetry_request.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = app_started_telemetry_request()

            assert telmetry_request["request_type"] == "app-started"
            assert telmetry_request["seq_id"] == -1
            assert len(telmetry_request["payload"]["dependencies"]) > 0
