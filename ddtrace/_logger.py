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
        - Logs are propagated up so that they appear in the application logs if a file path wasn't provided
        - Logs are routed to a file when DD_TRACE_LOG_FILE is specified, using the log level in DD_TRACE_LOG_FILE_LEVEL.
        - Child loggers inherit from the parent ddtrace logger

    Note(s):
        1) The ddtrace-run logs under commands/ddtrace_run do not follow DD_TRACE_LOG_FILE if DD_TRACE_DEBUG is enabled.
            This is because ddtrace-run calls ``logging.basicConfig()`` when DD_TRACE_DEBUG is enabled, so
            this configuration is not applied.
        2) Python 2: If the application is using DD_TRACE_DEBUG=true, logging will need to be configured,
            ie: ``logging.basicConfig()``.

    """
    ddtrace_logger = logging.getLogger("ddtrace")

    _configure_ddtrace_debug_logger(ddtrace_logger)
    _configure_ddtrace_file_logger(ddtrace_logger)


def _configure_ddtrace_debug_logger(logger):
    debug_enabled = asbool(os.environ.get("DD_TRACE_DEBUG", "false"))
    if debug_enabled:
        logger.setLevel(logging.DEBUG)
        logger.debug("debug mode has been enabled for the ddtrace logger")


def _configure_ddtrace_file_logger(logger):
    log_file_level = os.environ.get("DD_TRACE_LOG_FILE_LEVEL", "DEBUG").upper()
    try:
        file_log_level_value = getattr(logging, log_file_level)
    except AttributeError:
        raise ValueError(
            "DD_TRACE_LOG_FILE_LEVEL is invalid. Log level must be CRITICAL/ERROR/WARNING/INFO/DEBUG.",
            log_file_level,
        )

    log_path = os.environ.get("DD_TRACE_LOG_FILE")
    if log_path is not None:
        log_path = os.path.abspath(log_path)
        max_file_bytes = int(os.environ.get("DD_TRACE_LOG_FILE_SIZE_BYTES", DEFAULT_FILE_SIZE_BYTES))
        num_backup = 1
        ddtrace_file_handler = RotatingFileHandler(
            filename=log_path, mode="a", maxBytes=max_file_bytes, backupCount=num_backup
        )

        log_format = "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] - %(message)s"
        log_formatter = logging.Formatter(log_format)

        ddtrace_file_handler.setLevel(file_log_level_value)
        ddtrace_file_handler.setFormatter(log_formatter)
        logger.addHandler(ddtrace_file_handler)
        logger.debug("ddtrace logs will be routed to %s", log_path)
