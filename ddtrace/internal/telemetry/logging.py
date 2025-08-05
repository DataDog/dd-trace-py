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
            # Capture start up errors
            full_file_name = os.path.join(record.pathname, record.filename)
            self.telemetry_writer.add_error(1, record.msg, full_file_name, record.lineno)
