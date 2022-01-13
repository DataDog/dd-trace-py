import mock

from ddtrace.internal.telemetry.data import get_application
from ddtrace.internal.telemetry.data import get_host_info
from ddtrace.internal.telemetry.request import create_telemetry_request
from ddtrace.settings import _config as config


def test_create_telemetry_request():
    """validates the return value of _create_telemetry_request"""
    with mock.patch("ddtrace.internal.telemetry.request.monotonic") as t:
        t.return_value = 888366600
        with mock.patch("ddtrace.internal.telemetry.request.get_runtime_id") as get_rt_id:
            get_rt_id.return_value = "1234-567"

            telmetry_request = create_telemetry_request(payload={}, payload_type="", sequence_id=-1)
            assert telmetry_request == {
                "tracer_time": 888366600,
                "runtime_id": "1234-567",
                "api_version": "v2",
                "seq_id": -1,
                "application": get_application(config.service, config.version, config.env),
                "host": get_host_info(),
                "payload": {},
                "request_type": "",
            }
