"""
Instrumentation Telemetry API.
This is normally started automatically by ``ddtrace-run`` when the
``DD_INSTRUMENTATION_TELEMETRY_ENABLED`` variable is set.
To start the service manually, invoke the ``enable`` method::
    from ddtrace.internal import telemetry
    telemetry.enable()
"""

from ...internal import forksafe
from ..service import ServiceStatus
from .writer import TelemetryWriter


TELEMETRY_WRITER = TelemetryWriter()

__all__ = ["enable", "disable", "add_integration"]


def add_integration(integration_name, auto_enabled):
    # type: (str, bool) -> None
    """
    Creates and queues the names and settings of a patched module

    :param str integration_name: name of patched module
    :param bool auto_enabled: True if module is enabled in _monkey.PATCH_MODULES
    """
    TELEMETRY_WRITER.add_integration(integration_name, auto_enabled)


def _restart():
    # type: () -> None
    if TELEMETRY_WRITER.status == ServiceStatus.RUNNING:
        disable()
    enable()


def disable():
    # type: () -> None
    """
    Disable the telemetry collection service.
    Once disabled, telemetry collection can be re-enabled by calling ``enable`` again.
    """
    if TELEMETRY_WRITER.status == ServiceStatus.STOPPED:
        return

    forksafe.unregister(_restart)

    TELEMETRY_WRITER.stop()
    TelemetryWriter.enabled = False

    TELEMETRY_WRITER.join()


def enable():
    # type: () -> None
    """
    Enable the instrumentation telemetry collection service. If the service has already been
    activated before, this method does nothing. Use ``disable`` to turn off the telemetry collection service.
    """
    if TELEMETRY_WRITER.status == ServiceStatus.RUNNING:
        return

    TELEMETRY_WRITER.start()
    TelemetryWriter.enabled = True

    forksafe.register(_restart)

    TELEMETRY_WRITER.app_started_event()
