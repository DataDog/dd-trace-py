import time
from typing import Any
from typing import Dict
from typing import TypedDict

from ...runtime import get_runtime_id
from .application import APPLICATION
from .application import Application
from .host import HOST
from .host import Host
from .payload import Payload


RequestBody = TypedDict(
    "RequestBody",
    {
        "tracer_time": int,
        "runtime_id": str,
        "api_version": str,
        # seq_id (sequence id) should be incremented every time a telemetry
        # request is sent to the agent. This field will be used to monitor
        # dropped payloads and reorder requests on the backend
        "seq_id": int,
        "application": Application,
        "host": Host,
        "payload": Dict[Any, Any],
        "request_type": str,
    },
)
"""
Contains all the body fields required by v1
of the Telemetry Intake API
"""

TelemetryRequest = TypedDict(
    "TelemetryRequest",
    {
        "headers": Dict[str, str],
        "body": RequestBody,
    },
)
"""
Contains all the header and body fields required
to send a request to v1 of Telemetry Intake Service
"""


def create_telemetry_request(payload):
    # type: (Payload) -> TelemetryRequest
    """
    Initializes the required fields for a generic Telemetry Intake Request

    The payload object sets fields specific to one
    of the following event types:
    app-started, app-closed, app-integrations-changed,
    and generate-metrics
    """
    return {
        "headers": {
            "Content-type": "application/json",
            "DD-Telemetry-Request-Type": payload.request_type(),
            "DD-Telemetry-API-Version": "v1",
        },
        "body": {
            "tracer_time": int(time.time()),
            "runtime_id": get_runtime_id(),
            "api_version": "v1",
            "seq_id": 0,
            "application": APPLICATION,
            "host": HOST,
            "payload": payload.to_dict(),
            "request_type": payload.request_type(),
        },
    }
