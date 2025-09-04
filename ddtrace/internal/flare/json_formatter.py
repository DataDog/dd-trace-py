import json
import logging
import time
from typing import Optional


class StructuredJSONFormatter(logging.Formatter):
    """
    A structured JSON formatter for flare logs using the native orjson encoder.
    Converts log records to structured JSON format for better parsing and analysis.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Format a log record as structured JSON.

        Args:
            record: The log record to format

        Returns:
            JSON formatted log string
        """
        # Create structured log data
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "filename": record.filename,
            "module": record.module,
            "funcName": record.funcName,
            "lineno": record.lineno,
            "message": record.getMessage(),
            "thread": record.thread,
            "threadName": record.threadName,
            "process": record.process,
            "processName": record.processName,
        }

        # Add exception information if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add stack trace if present
        if record.stack_info:
            log_data["stack_info"] = self.formatStack(record.stack_info)

        # Add any extra fields from the log record
        # Skip standard logging fields that we've already included or don't need
        skip_fields = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "getMessage",
            "exc_info",
            "exc_text",
            "stack_info",
            "message",
        }

        for key, value in record.__dict__.items():
            if key not in skip_fields and not key.startswith("_"):
                if value is not None and not isinstance(value, (str, int, bool)):
                    try:
                        log_data[key] = str(value)
                    except Exception:
                        try:
                            log_data[key] = repr(value)
                        except Exception:
                            log_data[key] = "unserializable_value"
                else:
                    log_data[key] = value

        try:
            return json.dumps(log_data)
        except Exception as e:
            # Fallback to a basic JSON structure if native JSON fails
            fallback_data = {
                "timestamp": self.formatTime(record, self.datefmt),
                "level": record.levelname,
                "logger": record.name,
                "message": f"JSON formatting failed: {e}. Original message: {record.getMessage()}",
                "error": "json_formatting_failed",
            }
            return json.dumps(fallback_data)

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        """
        Format the time for the log record in ISO format.

        Args:
            record: The log record
            datefmt: Date format string (ignored, always uses ISO format)

        Returns:
            ISO formatted timestamp string
        """
        # Use ISO format for structured logging
        ct = time.gmtime(record.created)
        return time.strftime("%Y-%m-%dT%H:%M:%S", ct) + f".{int(record.msecs):03d}Z"
