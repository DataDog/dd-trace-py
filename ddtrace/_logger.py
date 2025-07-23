import logging
from os import path
from typing import Optional

from ddtrace.internal.telemetry import get_config
from ddtrace.internal.utils.formats import asbool


DD_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] {}- %(message)s".format(
    "[dd.service=%(dd.service)s dd.env=%(dd.env)s dd.version=%(dd.version)s"
    " dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s] "
)

DEFAULT_FILE_SIZE_BYTES = 15 << 20  # 15 MB


class LogInjectionState(object):
    # Log injection is disabled
    DISABLED = "false"
    # Log injection is enabled, but not yet configured
    ENABLED = "true"
    # Log injection is enabled and configured for structured logging
    # This value is deprecated, but kept for backwards compatibility
    STRUCTURED = "structured"


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
    if get_config("DD_TRACE_LOG_STREAM_HANDLER", True, asbool):
        ddtrace_logger.addHandler(logging.StreamHandler())

    _configure_ddtrace_debug_logger(ddtrace_logger)
    _configure_ddtrace_file_logger(ddtrace_logger)


def _configure_ddtrace_debug_logger(logger):
    if get_config("DD_TRACE_DEBUG", False, asbool):
        logger.setLevel(logging.DEBUG)


def _configure_ddtrace_file_logger(logger):
    log_file_level = get_config("DD_TRACE_LOG_FILE_LEVEL", "DEBUG").upper()
    try:
        file_log_level_value = getattr(logging, log_file_level)
    except AttributeError:
        raise ValueError(
            "DD_TRACE_LOG_FILE_LEVEL is invalid. Log level must be CRITICAL/ERROR/WARNING/INFO/DEBUG.",
            log_file_level,
        )
    max_file_bytes = get_config("DD_TRACE_LOG_FILE_SIZE_BYTES", DEFAULT_FILE_SIZE_BYTES, int)
    log_path = get_config("DD_TRACE_LOG_FILE")
    _add_file_handler(logger=logger, log_path=log_path, log_level=file_log_level_value, max_file_bytes=max_file_bytes)


def _add_file_handler(
    logger: logging.Logger,
    log_path: Optional[str],
    log_level: int,
    handler_name: Optional[str] = None,
    max_file_bytes: int = DEFAULT_FILE_SIZE_BYTES,
):
    ddtrace_file_handler = None
    if log_path is not None:
        log_path = path.abspath(log_path)
        num_backup = 1
        from logging.handlers import RotatingFileHandler

        ddtrace_file_handler = RotatingFileHandler(
            filename=log_path, mode="a", maxBytes=max_file_bytes, backupCount=num_backup
        )
        log_format = "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] - %(message)s"
        log_formatter = logging.Formatter(log_format)
        ddtrace_file_handler.setLevel(log_level)
        ddtrace_file_handler.setFormatter(log_formatter)
        if handler_name:
            ddtrace_file_handler.set_name(handler_name)
        logger.addHandler(ddtrace_file_handler)
        logger.debug("ddtrace logs will be routed to %s", log_path)
    return ddtrace_file_handler


def set_log_formatting():
    # type: () -> None
    """Sets the log format for the ddtrace logger."""
    ddtrace_logger = logging.getLogger("ddtrace")
    for handler in ddtrace_logger.handlers:
        handler.setFormatter(logging.Formatter(DD_LOG_FORMAT))


def get_log_injection_state(raw_config: Optional[str]) -> bool:
    """Returns the current log injection state."""
    if raw_config:
        normalized = raw_config.lower().strip()
        if normalized == LogInjectionState.STRUCTURED or normalized in ("true", "1"):
            return True
        elif normalized not in ("false", "0"):
            logging.warning(
                "Invalid log injection state '%s'. Expected 'true', 'false', or 'structured'. Defaulting to 'false'.",
                normalized,
            )
    return False
