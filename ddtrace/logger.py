import logging
from logging.handlers import RotatingFileHandler
import os


def configure_ddtrace_logger():
    # type: () -> None
    """
    """
    debug_log_level = os.environ.get("DD_TRACE_DEBUG", "false").lower() in ("true", "1")
    log_path = os.environ.get("DD_TRACE_LOG_FILE", None)
    max_file_bytes = os.environ.get("DD_TRACE_FILE_SIZE_BYTES", 15000000)
    num_backup = 1
    log_format = "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] {}- %(message)s"
    log_formatter = logging.Formatter(log_format)

    ddtrace_logger = logging.getLogger("ddtrace")
    ddtrace_logger.propagate = False

    if debug_log_level is False:
        ddtrace_logger.setLevel(logging.WARN)
    else:
        ddtrace_logger.setLevel(logging.DEBUG)

    if log_path is not None:
        ddtrace_handler = RotatingFileHandler(
            filename=log_path, mode="a", maxBytes=max_file_bytes, backupCount=num_backup
        )
        ddtrace_handler.setFormatter(log_formatter)
        ddtrace_logger.addHandler(ddtrace_handler)
        ddtrace_logger.debug("Debug mode has been enabled with debug logs logging to %s", log_path)
    else:
        ddtrace_handler = logging.StreamHandler()
        ddtrace_handler.setFormatter(log_formatter)
        ddtrace_logger.addHandler(ddtrace_handler)
        ddtrace_logger.debug("Debug mode has been enabled with debug logs logging to stderr")
