"""
Instrumentation Telemetry API.
This is normally started automatically by ``ddtrace-run`` when the
``DD_INSTRUMENTATION_TELEMETRY_ENABLED`` variable is set.
To start the service manually, invoke the ``enable`` method::
    from ddtrace.internal.telemetry import telemetry_writer
    telemetry_writer.enable()
"""
import os
import sys

from .writer import TelemetryWriter


telemetry_writer = TelemetryWriter()

__all__ = ["telemetry_writer"]


_ORIGINAL_EXCEPTHOOK = sys.excepthook


def _excepthook(tp, value, root_traceback):
    if root_traceback is not None:
        # Get the frame which raised the exception
        traceback = root_traceback
        while traceback.tb_next:
            traceback = traceback.tb_next

        lineno = traceback.tb_frame.f_code.co_firstlineno
        filename = traceback.tb_frame.f_code.co_filename
        telemetry_writer.add_error(1, str(value), filename, lineno)

        dir_parts = filename.split(os.path.sep)
        # Check if exception was raised in the  `ddtrace.contrib` package
        if "ddtrace" in dir_parts and "contrib" in dir_parts:
            ddtrace_index = dir_parts.index("ddtrace")
            contrib_index = dir_parts.index("contrib")
            # Check if the filename has the following format:
            # `../ddtrace/contrib/integration_name/..(subpath and/or file)...`
            if ddtrace_index + 1 == contrib_index and len(dir_parts) - 2 > contrib_index:
                integration_name = dir_parts[contrib_index + 1]
                telemetry_writer.add_count_metric(
                    "tracers",
                    "integration_errors",
                    1,
                    (("integration_name", integration_name), ("error_type", tp.__name__)),
                )
                error_msg = "{}:{} {}".format(filename, lineno, str(value))
                telemetry_writer.add_integration(integration_name, True, error_msg=error_msg)

        telemetry_writer.app_shutdown()
        telemetry_writer.disable()

    return _ORIGINAL_EXCEPTHOOK(tp, value, root_traceback)


def install_excepthook():
    """Install a hook that intercepts unhandled exception and send metrics about them."""
    sys.excepthook = _excepthook


def uninstall_excepthook():
    """Uninstall the global tracer except hook."""
    sys.excepthook = _ORIGINAL_EXCEPTHOOK
