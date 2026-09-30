from typing import Any

from ddtrace.internal.logger import get_logger


LOG = get_logger(__name__)


def sanitize_string(value: Any):
    """Coerce value to str or bytes for the profiling C++ layer.

    str and bytes pass through unchanged — the C++ / Rust side handles
    lossy UTF-8 conversion for bytes. Other types get a placeholder so
    frames remain visible in profiles rather than being silently dropped.
    """
    if isinstance(value, (str, bytes)):
        return value
    elif value is None:
        return ""
    LOG.warning("Got object of type '%s' instead of str during profile serialization", type(value).__name__)
    return "[invalid string]%s" % type(value).__name__
