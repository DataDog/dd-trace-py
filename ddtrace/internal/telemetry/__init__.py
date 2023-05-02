"""
Instrumentation Telemetry API.
This is normally started automatically by ``ddtrace-run`` when the
``DD_INSTRUMENTATION_TELEMETRY_ENABLED`` variable is set.
To start the service manually, invoke the ``enable`` method::
    from ddtrace.internal.telemetry import telemetry_writer
    telemetry_writer.enable()
"""
import sys

from .writer import TelemetryLogsMetricsWriter
from .writer import TelemetryWriter


telemetry_metrics_writer = TelemetryLogsMetricsWriter()
telemetry_writer = TelemetryWriter()

__all__ = ["telemetry_writer"]


_ORIGINAL_EXCEPTHOOK = sys.excepthook


def _excepthook(tp, value, traceback):
    try:
        telemetry_writer.add_error(1, str(value))
        sys.stdout.write(value)
        # telemetry_writer.enable()
    except Exception:
        # Required to avoid circular references, _excepthook should not raise an unhandled exception
        pass
    return _ORIGINAL_EXCEPTHOOK(tp, value, traceback)


def install_excepthook():
    """Install a hook that intercepts unhandled exception and send metrics about them."""
    sys.excepthook = _excepthook


def uninstall_excepthook():
    """Uninstall the global tracer except hook."""
    sys.excepthook = _ORIGINAL_EXCEPTHOOK
