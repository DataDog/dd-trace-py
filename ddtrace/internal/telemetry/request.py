from typing import Dict

from ddtrace.settings import _config as config

from ..compat import monotonic
from ..runtime import get_runtime_id
from .data import get_application
from .data import get_host_info


def get_headers(payload_type):
    # type: (str) -> Dict
    """
    Creates request headers
    """
    return {
        "Content-type": "application/json",
        "DD-Telemetry-Request-Type": payload_type,
        "DD-Telemetry-API-Version": "v2",
    }


def create_telemetry_request(payload, payload_type, sequence_id):
    # type: (Dict, str, int) -> Dict
    """
    Initializes the required fields for a generic Telemetry Intake Request

    :param Dict payload: stores a formatted telemetry request

    :param str payload_type: The payload_type denotes the type of telmetery request.
        Payload types accepted by telemetry/proxy v2: app-started, app-closed, app-integrations-changed

    :param int seq_id: seq_id is a counter representing the number of requests sent by the writer
    """
    return {
        "tracer_time": int(monotonic()),
        "runtime_id": get_runtime_id(),
        "api_version": "v2",
        "seq_id": sequence_id,
        "application": get_application(config.service, config.version, config.env),
        "host": get_host_info(),
        "payload": payload,
        "request_type": payload_type,
    }
