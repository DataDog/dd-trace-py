"""
Instrumentation Telemetry API.
This is normally started automatically by ``ddtrace-run`` when the
``DD_INSTRUMENTATION_TELEMETRY_ENABLED`` variable is set.
To start the service manually, invoke the ``enable`` method::
    from ddtrace.internal.telemetry import telemetry_lifecycle_writer
    telemetry_lifecycle_writer.enable()
"""
import sys

from .writer import TelemetryLogsMetricsWriter
from .writer import TelemetryWriter


telemetry_metrics_writer = TelemetryLogsMetricsWriter()
telemetry_lifecycle_writer = TelemetryWriter()

__all__ = ["telemetry_lifecycle_writer"]


_ORIGINAL_EXCEPTHOOK = sys.excepthook


def _excepthook(tp, value, traceback):
    filename = None
    lineno = None
    if traceback and traceback.tb_frame and traceback.tb_frame.f_code:
        lineno = traceback.tb_frame.f_code.co_firstlineno
        filename = traceback.tb_frame.f_code.co_filename
    telemetry_lifecycle_writer.add_error(1, str(value), filename, lineno)

    if not telemetry_lifecycle_writer.started and telemetry_lifecycle_writer.enable(start_worker_thread=False):
        # Starting/stopping the telemetry worker thread in a sys.excepthook causes deadlocks in gunicorn.
        # Here we avoid starting the telemetry worker thread by manually queuing and sending
        # app-started and app-closed events.
        telemetry_lifecycle_writer._app_started_event()
        telemetry_lifecycle_writer.on_shutdown()
        telemetry_lifecycle_writer.disable()
    return _ORIGINAL_EXCEPTHOOK(tp, value, traceback)


def install_excepthook():
    """Install a hook that intercepts unhandled exception and send metrics about them."""
    sys.excepthook = _excepthook


def uninstall_excepthook():
    """Uninstall the global tracer except hook."""
    sys.excepthook = _ORIGINAL_EXCEPTHOOK
