"""
Instrumentation Telemetry API.
This is normally started automatically by ``ddtrace-run`` when the
``DD_INSTRUMENTATION_TELEMETRY_ENABLED`` variable is set.
To start the service manually, invoke the ``enable`` method::
    from ddtrace.internal.telemetry import telemetry_writer
    telemetry_writer.enable()
"""
import os
import typing

from ddtrace.internal.telemetry.libdd.libtelemetery import LibDDTelemetry

from .writer import TelemetryMetricsWriter
from .writer import TelemetryWriter


telemetry_metrics_writer = TelemetryMetricsWriter()

if os.getenv("_DDTRACE_USE_LIBDD_TELEMETRY_WORKER") is not None:
    telemetry_writer = LibDDTelemetry()  # type: typing.Any
else:
    telemetry_writer = TelemetryWriter()

__all__ = ["telemetry_writer"]
