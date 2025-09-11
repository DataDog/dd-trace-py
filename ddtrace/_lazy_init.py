"""
Lazy initialization utilities for ddtrace.

This module contains the lazy initialization logic to defer side effects
until they are actually needed.
"""
import functools
import os


# Lazy initialization state
_logger_configured = False
_telemetry_initialized = False
_logger_hook_installed = False


def validate_logger_config():
    """
    Validate logger configuration without actually configuring the logger.

    This performs minimal validation on import to maintain backward compatibility
    where configuration errors are detected immediately.
    """
    log_file_level = os.environ.get("DD_TRACE_LOG_FILE_LEVEL")
    if log_file_level:
        # Import logging only when needed
        import logging

        log_file_level = log_file_level.upper()
        try:
            getattr(logging, log_file_level)
        except AttributeError:
            raise ValueError(
                "DD_TRACE_LOG_FILE_LEVEL is invalid. Log level must be CRITICAL/ERROR/WARNING/INFO/DEBUG.",
                log_file_level,
            )


def setup_lazy_logger_hook():
    """
    Set up a hook to lazily configure the ddtrace logger when it's first accessed.

    This patches logging.getLogger to intercept access to the 'ddtrace' logger
    and configure it automatically, but only on first access.
    """
    global _logger_hook_installed
    if _logger_hook_installed:
        return

    # Import logging only when setting up the hook
    import logging

    original_getLogger = logging.getLogger

    def patched_getLogger(name=None):
        """Patched version of logging.getLogger that configures ddtrace logger on first access."""
        if name == "ddtrace":
            # Restore original immediately to avoid recursion
            logging.getLogger = original_getLogger
            # Configure the logger lazily
            _ensure_logger_configured()
            # Return the now-configured logger
            return original_getLogger(name)

        return original_getLogger(name)

    logging.getLogger = patched_getLogger
    _logger_hook_installed = True


def _ensure_logger_configured():
    """Lazy initialization of ddtrace logger."""
    global _logger_configured
    if not _logger_configured:
        _ensure_telemetry_initialized()
        from ._logger import configure_ddtrace_logger

        configure_ddtrace_logger()
        _logger_configured = True


def _ensure_telemetry_initialized():
    """Lazy initialization of telemetry."""
    global _telemetry_initialized
    if not _telemetry_initialized:
        import ddtrace.internal.telemetry  # noqa: F401

        _telemetry_initialized = True


def ensure_initialized(func):
    """Decorator that ensures all necessary components are initialized before calling the function."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        _ensure_logger_configured()
        _ensure_telemetry_initialized()
        return func(*args, **kwargs)

    return wrapper


# Lazy config initialization
_config = None


def get_config():
    """Lazy config import."""
    global _config
    if _config is None:
        from .settings._config import config as _imported_config

        _config = _imported_config
    return _config


# Lazy tracer initialization
_tracer = None


@ensure_initialized
def get_tracer():
    """Lazy tracer import."""
    global _tracer
    if _tracer is None:
        from ddtrace.trace import tracer as _imported_tracer  # noqa: F401

        _tracer = _imported_tracer
    return _tracer


def get_deprecation_warning():
    """Lazy DDTraceDeprecationWarning import."""
    from .internal.utils.deprecations import DDTraceDeprecationWarning

    return DDTraceDeprecationWarning


def get_ddtrace_submodule(name):
    """
    Generic lazy submodule loader for any ddtrace submodule.

    This function can dynamically import any ddtrace submodule on-demand.
    For simple modules (ext, constants, etc.), it skips heavy initialization.
    For complex modules that need initialization, it ensures prerequisites.
    """
    # Light modules that don't need full initialization
    light_modules = {"ext", "constants", "version"}

    # Only do heavy initialization for modules that actually need it
    if name not in light_modules:
        _ensure_logger_configured()
        _ensure_telemetry_initialized()

    try:
        # Use importlib for dynamic import
        import importlib

        module_name = f"ddtrace.{name}"
        return importlib.import_module(module_name)
    except ImportError:
        # Re-raise with a more helpful message
        raise ImportError(f"No module named 'ddtrace.{name}'")
