"""
Instrumentation Telemetry API.
This is normally started automatically when ``ddtrace`` is imported. It can be disabled by setting
``DD_INSTRUMENTATION_TELEMETRY_ENABLED`` variable to ``False``.
"""
import os

if os.environ.get("DD_INSTRUMENTATION_TELEMETRY_ENABLED", "true").lower() in ("true", "1"):
    from .writer import TelemetryWriter

    telemetry_writer = TelemetryWriter()  # type: TelemetryWriter

else:
    from .noop_writer import NoOpTelemetryWriter

    telemetry_writer = NoOpTelemetryWriter()

__all__ = ["telemetry_writer"]
