import logging
from logging.handlers import RotatingFileHandler

from ddtrace.internal.utils.formats import asbool
from ddtrace.utils.formats import get_env


def configure_logger():
    logging_level = asbool(get_env("trace", "debug", default=False))
    log_path = get_env("trace", "log", "file", default="default")
    max_file_bytes = get_env("trace", "log", "file", "size", "bytes", default=15000000)
    num_backup = 1
    log_format = "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] {}- %(message)s"

    ddtrace_logger = logging.getLogger("ddtrace")

    if logging_level:
        if log_path != "default":
            ddtrace_handler = RotatingFileHandler(
                filename=log_path, mode="a", maxBytes=max_file_bytes, backupCount=num_backup
            )

            log_format = logging.Formatter(log_format)
            ddtrace_handler.setFormatter(log_format)

            ddtrace_logger.addHandler(ddtrace_handler)
            ddtrace_handler.setLevel(logging.DEBUG)
            ddtrace_logger.propagate = False

        ddtrace_logger.debug("Debug mode has been enabled with debug logs logging to %s", log_path)
