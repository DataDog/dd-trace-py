import logging
import os
import re


class DDTelemetryErrorHandler(logging.Handler):
    CWD = os.getcwd()

    def __init__(self, telemetry_writer):
        self.telemetry_writer = telemetry_writer
        super().__init__()

    def emit(self, record: logging.LogRecord) -> None:
        """This function will:
        - Log all records with a level of ERROR or higher with telemetry
        """
        if record.levelno >= logging.ERROR:
            if getattr(record, "send_to_telemetry", None) in (None, True):
                # we do not want to send the [x skipped] part to telemetry
                msg = re.sub(r"\s*\[\d+ skipped\]$", "", record.msg)
                self.telemetry_writer.add_error_log(msg, record.exc_info)
