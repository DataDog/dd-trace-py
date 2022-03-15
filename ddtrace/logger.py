import logging
from logging.handlers import RotatingFileHandler
import os


DEFAULT_FILE_SIZE_BYTES = 15 << 20  # 15 MB


def configure_ddtrace_logger():
    # type: () -> None
    """ """
    debug_enabled = os.environ.get("DD_TRACE_DEBUG", "false").lower() in ("true", "1")
    log_path = os.environ.get("DD_TRACE_LOG_FILE", None)
    max_file_bytes = int(os.environ.get("DD_TRACE_FILE_SIZE_BYTES", DEFAULT_FILE_SIZE_BYTES))
    num_backup = 1
    log_format = "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] {}- %(message)s"
    log_formatter = logging.Formatter(log_format)

    ddtrace_logger = logging.getLogger("ddtrace")
    ddtrace_logger.propagate = False

    if debug_enabled is False:
        ddtrace_logger.setLevel(logging.WARN)
    else:
        ddtrace_logger.setLevel(logging.DEBUG)

    if log_path is not None:
        ddtrace_file_handler = RotatingFileHandler(
            filename=log_path, mode="a", maxBytes=max_file_bytes, backupCount=num_backup
        )
        ddtrace_file_handler.setFormatter(log_formatter)
        ddtrace_logger.addHandler(ddtrace_file_handler)
        ddtrace_logger.debug("Debug mode has been enabled with debug logs logging to %s", log_path)
    else:
        ddtrace_stream_handler = logging.StreamHandler()
        ddtrace_stream_handler.setFormatter(log_formatter)
        ddtrace_logger.addHandler(ddtrace_stream_handler)
        ddtrace_logger.debug("Debug mode has been enabled with debug logs logging to stderr")
