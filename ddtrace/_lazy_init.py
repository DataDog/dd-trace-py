"""
Lazy initialization utilities for ddtrace.

This module contains the lazy initialization logic to defer side effects
until they are actually needed.
"""
import functools


# Lazy initialization state
_logger_configured = False
_telemetry_initialized = False
_python_version_checked = False


def _ensure_logger_configured():
    """Lazy initialization of ddtrace logger."""
    global _logger_configured
    if not _logger_configured:
        from ._logger import configure_ddtrace_logger

        configure_ddtrace_logger()
        _logger_configured = True


def _ensure_telemetry_initialized():
    """Lazy initialization of telemetry."""
    global _telemetry_initialized
    if not _telemetry_initialized:
        import ddtrace.internal.telemetry  # noqa: F401

        _telemetry_initialized = True


def _ensure_python_version_checked():
    """Lazy Python version checking."""
    global _python_version_checked
    if not _python_version_checked:
        from ddtrace.vendor import debtcollector

        from .internal.compat import PYTHON_VERSION_INFO
        from .internal.utils.deprecations import DDTraceDeprecationWarning

        if PYTHON_VERSION_INFO < (3, 8):
            deprecation_message = (
                "Support for ddtrace with Python version %d.%d is deprecated and will be removed in 3.0.0."
            )
            if PYTHON_VERSION_INFO < (3, 7):
                deprecation_message = "Support for ddtrace with Python version %d.%d was removed in 2.0.0."
            debtcollector.deprecate(
                (deprecation_message % (PYTHON_VERSION_INFO[0], PYTHON_VERSION_INFO[1])),
                category=DDTraceDeprecationWarning,
            )
        _python_version_checked = True


def ensure_initialized(func):
    """Decorator that ensures all necessary components are initialized before calling the function."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        _ensure_logger_configured()
        _ensure_telemetry_initialized()
        _ensure_python_version_checked()
        return func(*args, **kwargs)

    return wrapper


# Lazy config initialization
_config = None


def get_config():
    """Lazy config import."""
    global _config
    if _config is None:
        _ensure_telemetry_initialized()  # Config requires telemetry
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
    _ensure_python_version_checked()
    from .internal.utils.deprecations import DDTraceDeprecationWarning

    return DDTraceDeprecationWarning
