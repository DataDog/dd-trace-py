from typing import Any
from typing import Dict
from typing import List

from ddtrace.internal.compat import TypedDict

from ...compat import monotonic
from ..runtime import get_runtime_id
from .data import APPLICATION
from .data import Application
from .data import HOST
from .data import Host
from .data import Integration
from .events import AppClosedEvent
from .events import AppIntegrationsChangedEvent
from .events import AppStartedEvent
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


def _create_telemetry_request(event, event_type, seq_id):
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
            "tracer_time": int(monotonic()),
            "runtime_id": get_runtime_id(),
            "api_version": "v1",
            "seq_id": seq_id,
            "application": APPLICATION,
            "host": HOST,
            "payload": event,
            "request_type": event_type,
        },
    }


def app_started_telemetry_request():
    # type: () -> TelemetryRequest
    """
    returns a TelemetryRequest which contains a list of application dependencies and configurations
    """

    event = AppStartedEvent(
        dependencies=get_app_dependencies(),
        configurations=get_app_configurations(),  # will set configurations in future comits
    )
    return _create_telemetry_request(event, "app-started", 0)


def app_closed_telemetry_request(seq_id):
    # type: (int) -> TelemetryRequest
    """
    returns a TelemetryRequest which notifies the agent that an application instance has terminated

    :param seq_id int: arg is a counter representing the number of requests sent by the writer
    """
    event = AppClosedEvent()
    return _create_telemetry_request(event, "app-closed", seq_id)


def app_integrations_changed_telemetry_request(integrations, seq_id):
    # type: (List[Integration], int) -> TelemetryRequest
    """
    returns a TelemetryRequest which sends a list of configured integrations to the agent

    :param seq_id int: arg is a counter representing the number of requests sent by the writer
    """
    event = AppIntegrationsChangedEvent(integrations=integrations)
    return _create_telemetry_request(event, "app-integrations-changed", seq_id)
