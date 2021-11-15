import os
from typing import Optional

from ...internal.telemetry.data.integration import create_integration
from ...internal.utils.formats import asbool
from .data.integration import Integration
from .data.metrics import Series
from .data.payload import AppStartedPayload
from .data.telemetry_request import TelemetryRequest
from .data.telemetry_request import create_telemetry_request
from .telemetry_writer import TelemetryWriter


_TELEMETRY_WRITER = None  # type: Optional[TelemetryWriter]

TELEMETRY_ENABLED = asbool(os.environ.get("DD_INSTRUMENTATION_TELEMETRY_ENABLED", default=False))  # type: bool

if TELEMETRY_ENABLED:
    _TELEMETRY_WRITER = TelemetryWriter()

    appstarted_request = create_telemetry_request(payload=AppStartedPayload())
    _TELEMETRY_WRITER.requests.append(appstarted_request)


def queue_telemetry_event(request_body):
    # type: (TelemetryRequest) -> None
    if TELEMETRY_ENABLED:
        _TELEMETRY_WRITER.add_request(request_body)


def queue_integration(module, error_message="", compatible=""):
    # type: (str, str, str) -> None
    if TELEMETRY_ENABLED:
        integration = create_integration(name=module, errors=error_message, compatible=compatible)
        _TELEMETRY_WRITER.add_integration(integration)


def queue_metric(series):
    # type: (Series) -> None
    if TELEMETRY_ENABLED:
        _TELEMETRY_WRITER.add_metric(series)
