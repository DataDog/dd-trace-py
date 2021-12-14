import mock

from ddtrace.internal.telemetry.data import APPLICATION
from ddtrace.internal.telemetry.data import HOST
from ddtrace.internal.telemetry.events import AppClosedPayload
from ddtrace.internal.telemetry.telemetry_request import create_telemetry_request


def test_create_telemetry_request():
    """validates the return value of create_telemetry_request"""
    payload = AppClosedPayload()
    seq_id = 1

    with mock.patch("time.time") as t:
        t.return_value = 888366600
        with mock.patch("ddtrace.internal.telemetry.telemetry_request.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = create_telemetry_request(payload, "app-closed", seq_id)
            assert telmetry_request == {
                "headers": {
                    "Content-type": "application/json",
                    "DD-Telemetry-Request-Type": "app-closed",
                    "DD-Telemetry-API-Version": "v1",
                    "DD-API-KEY": "",
                },
                "body": {
                    "tracer_time": 888366600,
                    "runtime_id": "1234-567",
                    "api_version": "v1",
                    "seq_id": seq_id,
                    "application": APPLICATION,
                    "host": HOST,
                    "payload": {},
                    "request_type": "app-closed",
                },
            }


# def test_payload_request_type():
#     """validates the return value of Payload.request_type"""
#     assert AppClosedPayload().request_type() == "app-closed"
#     assert AppStartedPayload().request_type() == "app-started"
#     assert AppIntegrationsChangedPayload([]).request_type() == "app-integrations-changed"
