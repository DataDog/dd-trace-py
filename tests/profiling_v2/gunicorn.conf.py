from datetime import datetime
from datetime import timezone
import logging


def post_fork(server, worker):
    """Log the startup time of each worker."""
    logging.info("Worker %s started", worker.pid)


def post_worker_init(worker):
    logging.info("Worker %s initialized", worker.pid)


class CustomFormatter(logging.Formatter):
    """Custom formatter to include timezone offset in the log message."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone()
        milliseconds = int(record.msecs)
        offset = dt.strftime("%z")  # Get timezone offset in the form +0530
        if datefmt:
            formatted_time = dt.strftime(datefmt)
        else:
            formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")

        # Add milliseconds and timezone offset
        offset = dt.strftime("%z")  # Timezone offset in the form +0530
        return f"{formatted_time}.{milliseconds:03d} {offset}"


logconfig_dict = {
    "version": 1,
    "formatters": {
        "default": {
            "()": CustomFormatter,  # Use the custom formatter
            "format": "[%(asctime)s] [%(process)d] [%(levelname)s] %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
    },
    "loggers": {
        "": {  # root logger
            "handlers": ["console"],
            "level": "INFO",
        },
        "gunicorn.error": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "gunicorn.access": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {
        "level": "INFO",
        "handlers": ["console"],
    },
}
