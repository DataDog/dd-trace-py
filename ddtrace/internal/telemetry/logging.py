import logging
import os
import traceback
from typing import Union

from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL


class DDTelemetryLogHandler(logging.Handler):
    CWD = os.getcwd()

    def __init__(self, telemetry_writer):
        self.telemetry_writer = telemetry_writer
        super().__init__()

    def emit(self, record: logging.LogRecord) -> None:
        """This function will:
        - Log all records with a level of ERROR or higher with telemetry
        - Log all caught exception originated from ddtrace.contrib modules
        """
        if record.levelno >= logging.ERROR:
            # Capture start up errors
            full_file_name = os.path.join(record.pathname, record.filename)
            self.telemetry_writer.add_error(1, record.msg % record.args, full_file_name, record.lineno)

        # Capture errors logged in the ddtrace integrations
        if record.name.startswith("ddtrace.contrib"):
            telemetry_level = (
                TELEMETRY_LOG_LEVEL.ERROR
                if record.levelno >= logging.ERROR
                else TELEMETRY_LOG_LEVEL.WARNING
                if record.levelno == logging.WARNING
                else TELEMETRY_LOG_LEVEL.DEBUG
            )
            # Only collect telemetry for logs with a traceback
            stack_trace = self._format_stack_trace(record.exc_info)
            if stack_trace is not None:
                # Report only exceptions with a stack trace
                self.telemetry_writer.add_log(
                    telemetry_level,
                    record.msg,
                    stack_trace=stack_trace,
                )

    def _format_stack_trace(self, exc_info) -> Union[str, None]:
        if exc_info is None:
            return None

        exc_type, exc_value, exc_traceback = exc_info
        if exc_traceback:
            tb = traceback.extract_tb(exc_traceback)
            formatted_tb = ["Traceback (most recent call last):"]
            for filename, lineno, funcname, srcline in tb:
                if self._should_redact(filename):
                    formatted_tb.append("  <REDACTED>")
                else:
                    relative_filename = self._format_file_path(filename)
                    formatted_line = f'  File "{relative_filename}", line {lineno}, in {funcname}\n    {srcline}'
                    formatted_tb.append(formatted_line)
            if exc_type:
                formatted_tb.append(f"{exc_type.__module__}.{exc_type.__name__}: {exc_value}")
            return "\n".join(formatted_tb)

        return None

    def _should_redact(self, filename: str) -> bool:
        return "ddtrace" not in filename

    def _format_file_path(self, filename):
        try:
            return os.path.relpath(filename, start=self.CWD)
        except ValueError:
            return filename
