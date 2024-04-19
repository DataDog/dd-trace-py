from sys import version_info
from typing import Any  # noqa:F401

from ddtrace.internal.logger import get_logger


LOG = get_logger(__name__)


# 3.11 and above
def _sanitize_string_check(value):
    # type: (Any) -> str
    if isinstance(value, str):
        return value
    elif value is None:
        return ""
    try:
        return value.decode("utf-8", "ignore")
    except Exception:
        LOG.warning("Got object of type '%s' instead of str during profile serialization", type(value).__name__)
        return "[invalid string]%s" % type(value).__name__


# 3.10 and below (the noop version)
def _sanitize_string_identity(value):
    # type: (Any) -> str
    return value or ""


# Assign based on version
sanitize_string = _sanitize_string_check if version_info[:2] > (3, 10) else _sanitize_string_identity
