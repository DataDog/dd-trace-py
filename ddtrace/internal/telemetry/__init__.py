from ...internal import forksafe
from .writer import TelemetryWriter


TELEMETRY_WRITER = TelemetryWriter()

__all__ = ["enable", "disable", "add_integration"]


def add_integration(integration_name):
    # type: (str) -> None
    TELEMETRY_WRITER.add_integration(integration_name)


def _restart():
    # type: () -> None
    disable()
    enable()


def disable():
    # type: () -> None
    forksafe.unregister(_restart)

    TELEMETRY_WRITER.stop()
    TelemetryWriter.enabled = False

    TELEMETRY_WRITER.join()


def enable():
    # type: () -> None
    TELEMETRY_WRITER.start()
    TelemetryWriter.enabled = True

    forksafe.register(_restart)

    TELEMETRY_WRITER.app_started_event()
