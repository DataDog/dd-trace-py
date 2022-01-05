from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.compat import TypedDict

from ...compat import monotonic
from ..runtime import get_runtime_id
from .data import APPLICATION
from .data import HOST


# Contains all the body fields required by v1 of the Telemetry Intake API
RequestBody = TypedDict(
    "RequestBody",
    {
        "tracer_time": int,
        "runtime_id": str,
        "api_version": str,
        # seq_id (sequence id) should be incremented every time a telemetry request is
        # sent to the agent. This field will be used to monitor dropped payloads and
        # reorder requests on the backend.
        # The value is set in TelemetryWriter.add_event() to ensure seq_id is monotonically increasing
        "seq_id": Optional[int],
        "application": Dict,
        "host": Dict,
        "payload": Dict,
        "request_type": str,
    },
)

# Contains all the header and body fields required to send a request to v1 of
# the Telemetry Intake Service
TelemetryRequest = TypedDict(
    "TelemetryRequest",
    {
        "headers": Dict[str, str],
        "body": RequestBody,
    },
)


def _create_telemetry_request(payload, payload_type, seq_id):
    # type: (Dict, str, Optional[int]) -> TelemetryRequest
    """
    Initializes the required fields for a generic Telemetry Intake Request

    :param Payload payload: The payload object sets fields specific to one of the following payload types:
        app-started, app-closed, app-integrations-changed, and generate-metrics

    :param seq_id int: arg is a counter representing the number of requests sent by the writer
    """
    return {
        "headers": {
            "Content-type": "application/json",
            "DD-Telemetry-Request-Type": payload_type,
            "DD-Telemetry-API-Version": "v1",
        },
        "body": {
            "tracer_time": int(monotonic()),
            "runtime_id": get_runtime_id(),
            "api_version": "v1",
            "seq_id": seq_id,
            "application": APPLICATION,
            "host": HOST,
            "payload": payload,
            "request_type": payload_type,
        },
    }


def app_started_telemetry_request():
    # type: () -> TelemetryRequest
    """
    Returns a TelemetryRequest which contains a list of application dependencies and configurations
    """
    import pkg_resources

    payload = {
        "dependencies": [{"name": pkg.project_name, "version": pkg.version} for pkg in pkg_resources.working_set],
        "configurations": {},
    }
    return _create_telemetry_request(payload, "app-started", 0)


def app_closed_telemetry_request(seq_id=None):
    # type: (Optional[int]) -> TelemetryRequest
    """
    Returns a TelemetryRequest which notifies the agent that an application instance has terminated

    :param seq_id int: arg is a counter representing the number of requests sent by the writer
    """
    payload = {}  # type: Dict
    return _create_telemetry_request(payload, "app-closed", seq_id)


def app_integrations_changed_telemetry_request(integrations, seq_id=None):
    # type: (List[Dict], Optional[int]) -> TelemetryRequest
    """
    Returns a TelemetryRequest which sends a list of configured integrations to the agent

    :param seq_id int: arg is a counter representing the number of requests sent by the writer
    """
    payload = {
        "integrations": integrations,
    }
    return _create_telemetry_request(payload, "app-integrations-changed", seq_id)
