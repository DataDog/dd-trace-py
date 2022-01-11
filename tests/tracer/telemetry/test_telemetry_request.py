import mock

from ddtrace.internal.telemetry.data import get_application
from ddtrace.internal.telemetry.data import get_host
from ddtrace.internal.telemetry.telemetry_request import create_telemetry_request
from ddtrace.settings import _config as config


def test_create_telemetry_request():
    """validates the return value of _create_telemetry_request"""
    with mock.patch("ddtrace.internal.telemetry.telemetry_request.monotonic") as t:
        t.return_value = 888366600
        with mock.patch("ddtrace.internal.telemetry.telemetry_request.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = create_telemetry_request(payload={}, payload_type="", sequence_id=-1)
            assert telmetry_request == {
                "tracer_time": 888366600,
                "runtime_id": "1234-567",
                "api_version": "v2",
                "seq_id": -1,
                "application": get_application(config.service, config.version, config.env),
                "host": get_host(),
                "payload": {},
                "request_type": "",
            }


def test_app_closed_telemetry_request():
    """validates the return value of _create_telemetry_request"""
    with mock.patch("time.time") as t:
        t.return_value = 888366600
        with mock.patch("ddtrace.internal.telemetry.telemetry_request.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = create_telemetry_request({}, "app-closed", 100)

            assert telmetry_request["request_type"] == "app-closed"
            assert telmetry_request["seq_id"] == 100
            assert telmetry_request["payload"] == {}


def test_app_integrations_changed_telemetry_request():
    """validates the return value of _create_telemetry_request"""

    integrations_payload = {
        "integrations": [
            {
                "name": "integration-name",
                "version": "",
                "enabled": True,
                "auto_enabled": True,
                "compatible": "",
                "error": "",
            }
        ]
    }

    with mock.patch("time.time") as t:
        t.return_value = 888366600
        with mock.patch("ddtrace.internal.telemetry.telemetry_request.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = create_telemetry_request(integrations_payload, "app-integrations-changed", 1)

            assert telmetry_request["request_type"] == "app-integrations-changed"
            assert telmetry_request["seq_id"] == 1
            assert telmetry_request["payload"] == integrations_payload


def test_app_started_telemetry_request():
    """validates the return value of _create_telemetry_request"""

    app_started_payload = {
        "dependencies": {
            "name": "dep-name",
            "version": "",
        },
        "configurations": {},
    }

    with mock.patch("time.time") as t:
        t.return_value = 888366600
        with mock.patch("ddtrace.internal.telemetry.telemetry_request.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = create_telemetry_request(app_started_payload, "app-started", 2)

            assert telmetry_request["request_type"] == "app-started"
            assert telmetry_request["seq_id"] == 2
            assert len(telmetry_request["payload"]["dependencies"]) > 0
