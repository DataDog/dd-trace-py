from typing import Dict

from ddtrace.settings import _config as config

from ..compat import monotonic
from ..runtime import get_runtime_id
from .data import get_application
from .data import get_host


def create_telemetry_request(payload, payload_type, sequence_id):
    # type: (Dict, str, int) -> Dict
    """
    Initializes the required fields for a generic Telemetry Intake Request

    :param Payload payload: The payload object sets fields specific to one of the following payload types:
        app-started, app-closed, app-integrations-changed, and generate-metrics
    """
    return {
        "tracer_time": int(monotonic()),
        "runtime_id": get_runtime_id(),
        "api_version": "v2",
        "seq_id": sequence_id,
        "application": get_application(config.service, config.version, config.env),
        "host": get_host(),
        "payload": payload,
        "request_type": payload_type,
    }
