import logging
import os


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
                self.telemetry_writer.add_error_log(record.msg, record.exc_info)
