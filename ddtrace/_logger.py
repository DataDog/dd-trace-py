import logging
from logging.handlers import RotatingFileHandler
import os

from ddtrace.internal.utils.formats import asbool


DEFAULT_FILE_SIZE_BYTES = 15 << 20  # 15 MB


def configure_ddtrace_logger():
    # type: () -> None
    """Configures ddtrace log levels and file paths.
    By default, when debug hasn't been enabled and a file hasn't been specified:
        - Inherit from the root logger in the logging module

    In debug mode:
        - Propagate logs up so logs show up in the application logs when a file path wasn't provided
        - Logs are routed to a file when DD_TRACE_LOG_FILE is specified
        - Child loggers inherit from the parent ddtrace logger

    Note: The ddtrace-run logs under commands/ddtrace-run do not follow DD_TRACE_LOG_FILE
        if DD_TRACE_DEBUG is enabled.

    """
    debug_enabled = asbool(os.environ.get("DD_TRACE_DEBUG", "false"))
    log_path = os.environ.get("DD_TRACE_LOG_FILE")
    max_file_bytes = int(os.environ.get("DD_TRACE_FILE_SIZE_BYTES", DEFAULT_FILE_SIZE_BYTES))
    num_backup = 1
    log_format = "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] - %(message)s"
    log_formatter = logging.Formatter(log_format)

    ddtrace_logger = logging.getLogger("ddtrace")

    if debug_enabled is True:
        ddtrace_logger.setLevel(logging.DEBUG)
        ddtrace_logger.debug("Debug mode has been enabled for the ddtrace logger.")

    if log_path is not None:
        ddtrace_file_handler = RotatingFileHandler(
            filename=log_path, mode="a", maxBytes=max_file_bytes, backupCount=num_backup
        )
        if debug_enabled is False:
            ddtrace_file_handler.setLevel(logging.WARN)
        else:
            ddtrace_file_handler.setLevel(logging.DEBUG)
        ddtrace_file_handler.setFormatter(log_formatter)
        ddtrace_logger.addHandler(ddtrace_file_handler)
        ddtrace_logger.debug("Debug mode has been enabled to route logs to %s", log_path)
