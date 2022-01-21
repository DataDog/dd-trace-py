"""
Instrumentation Telemetry API.
This is normally started automatically by ``ddtrace-run`` when the
``DD_INSTRUMENTATION_TELEMETRY_ENABLED`` variable is set.
To start the service manually, invoke the ``enable`` method::
    from ddtrace.internal.telemetry import TELEMETRY_WRITER
    TELEMETRY_WRITER.enable()
"""
from .writer import TelemetryWriter


TELEMETRY_WRITER = TelemetryWriter()

__all__ = ["TELEMETRY_WRITER"]
