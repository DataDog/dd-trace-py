from typing import Dict
from typing import List
from typing import Optional

from ddtrace.settings import _config as config

from ..compat import monotonic
from ..runtime import get_runtime_id
from .data import get_application
from .data import get_host


def _create_telemetry_request(payload, payload_type, seq_id):
    # type: (Dict, str, Optional[int]) -> Dict
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
            "application": get_application(config.service, config.version, config.env),
            "host": get_host(),
            "payload": payload,
            "request_type": payload_type,
        },
    }


def app_started_telemetry_request():
    # type: () -> Dict
    """
    Returns a Telemetry request which contains a list of application dependencies and configurations
    """
    import pkg_resources

    payload = {
        "dependencies": [{"name": pkg.project_name, "version": pkg.version} for pkg in pkg_resources.working_set],
        "configurations": {},
    }
    return _create_telemetry_request(payload, "app-started", 0)


def app_closed_telemetry_request(seq_id=None):
    # type: (Optional[int]) -> Dict
    """
    Returns a Telemetry request which notifies the agent that an application instance has terminated

    :param seq_id int: arg is a counter representing the number of requests sent by the writer
    """
    payload = {}  # type: Dict
    return _create_telemetry_request(payload, "app-closed", seq_id)


def app_integrations_changed_telemetry_request(integrations, seq_id=None):
    # type: (List[Dict], Optional[int]) -> Dict
    """
    Returns a Telemetry request which sends a list of configured integrations to the agent

    :param seq_id int: arg is a counter representing the number of requests sent by the writer
    """
    payload = {
        "integrations": integrations,
    }
    return _create_telemetry_request(payload, "app-integrations-changed", seq_id)
