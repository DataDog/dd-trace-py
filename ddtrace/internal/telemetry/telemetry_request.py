import time
from typing import Any
from typing import Dict
from typing import TypedDict

from ..runtime import get_runtime_id
from .data import APPLICATION
from .data import Application
from .data import HOST
from .data import Host
from .events import AppClosedPayload
from .events import AppIntegrationsChangedPayload
from .events import AppStartedPayload
from .events import Event
from .events import get_app_configurations
from .events import get_app_dependencies


# Contains all the body fields required by v1 of the Telemetry Intake API
RequestBody = TypedDict(
    "RequestBody",
    {
        "tracer_time": int,
        "runtime_id": str,
        "api_version": str,
        # seq_id (sequence id) should be incremented every time a telemetry request is
        # sent to the agent. This field will be used to monitor dropped payloads and
        # reorder requests on the backend
        "seq_id": int,
        "application": Application,
        "host": Host,
        "payload": Dict[Any, Any],
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


def create_telemetry_request(event, event_type, seq_id):
    # type: (Event, str, int) -> TelemetryRequest
    """
    Initializes the required fields for a generic Telemetry Intake Request

    :param Payload payload: The payload object sets fields specific to one of the following event types:
        app-started, app-closed, app-integrations-changed, and generate-metrics

    :param seq_id int: arg is a counter representing the number of requests sent by the writer
    """
    return {
        "headers": {
            "Content-type": "application/json",
            "DD-Telemetry-Request-Type": event_type,
            "DD-Telemetry-API-Version": "v1",
            "DD-API-KEY": "",
        },
        "body": {
            "tracer_time": int(time.time()),
            "runtime_id": get_runtime_id(),
            "api_version": "v1",
            "seq_id": seq_id,
            "application": APPLICATION,
            "host": HOST,
            "payload": event,
            "request_type": event_type,
        },
    }


def app_started_telemetry_request(seq_id=0):
    """returns a TelemetryRequest which contains a list of application dependencies and configurations"""

    payload = {
        "dependencies": get_app_dependencies(),
        "configurations": get_app_configurations(),  # will set configurations in future comits
    }  # type: AppStartedPayload
    return create_telemetry_request(payload, "app-closed", seq_id)


def app_closed_telemetry_request(seq_id=0):
    """returns a TelemetryRequest which notifies the agent that an application instance has terminated"""
    payload = {}  # type: AppClosedPayload
    return create_telemetry_request(payload, "app-started", seq_id)


def app_integrations_changed_telemetry_request(integrations, seq_id=0):
    """returns a TelemetryRequest which sends a list of configured integrations to the agent"""
    payload = {
        "integrations": integrations,
    }  # type: AppIntegrationsChangedPayload
    return create_telemetry_request(payload, "app-integrations-changed", seq_id)
