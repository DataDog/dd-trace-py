import logging
from logging.handlers import RotatingFileHandler
import os

from ddtrace.internal.utils.formats import asbool


DEFAULT_FILE_SIZE_BYTES = 15 << 20  # 15 MB


def configure_ddtrace_logger():
    # type: () -> None
    """Configures ddtrace log levels and file paths.

    Customization is possible with the environment variables:
        ``DD_TRACE_DEBUG``, ``DD_TRACE_LOG_FILE_LEVEL``, and ``DD_TRACE_LOG_FILE``

    By default, when none of the settings have been changed, ddtrace loggers
        inherit from the root logger in the logging module and no logs are written to a file.

    When DD_TRACE_DEBUG has been enabled:
        - Propagate logs up so logs show up in the application logs if a file path wasn't provided
        - Logs are routed to a file when DD_TRACE_LOG_FILE is specified, using the log level in DD_TRACE_LOG_FILE_LEVEL.
        - Child loggers inherit from the parent ddtrace logger

    Note: The ddtrace-run logs under commands/ddtrace-run do not follow DD_TRACE_LOG_FILE
        if DD_TRACE_DEBUG is enabled.

    """
    debug_enabled = asbool(os.environ.get("DD_TRACE_DEBUG", "false"))

    log_file_level = os.environ.get("DD_TRACE_LOG_FILE_LEVEL", "DEBUG")
    file_log_level_value = logging.DEBUG
    try:
        file_log_level_value = getattr(logging, log_file_level.upper())
    except AttributeError:
        raise ValueError("Unknown log level. Logging level must be CRITICAL/ERROR/WARNING/INFO/DEBUG. Detected logging level was ", str(log_file_level))

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
        log_path = os.path.abspath(log_path)
        ddtrace_file_handler = RotatingFileHandler(
            filename=log_path, mode="a", maxBytes=max_file_bytes, backupCount=num_backup
        )

        ddtrace_file_handler.setLevel(file_log_level_value)
        ddtrace_file_handler.setFormatter(log_formatter)
        ddtrace_logger.addHandler(ddtrace_file_handler)
        ddtrace_logger.debug("ddtrace logs will be routed to %s", log_path)
