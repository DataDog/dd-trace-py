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
        "seq_id": int,
        "application": Application,
        "host": Host,
        "payload": Dict[Any, Any],
        "request_type": str,
    },
)

TelemetryRequest = TypedDict(
    "TelemetryRequest",
    {
        "headers": Dict[str, str],
        "body": RequestBody,
    },
)


def create_telemetry_request(payload):
    # type: (Payload) -> TelemetryRequest
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
