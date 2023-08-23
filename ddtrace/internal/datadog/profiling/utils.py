from sys import version_info
from typing import Any


# 3.11 and above
def _sanitize_string_check(value):
    # type: (Any) -> str
    from ddtrace.internal.logger import get_logger

    LOG = get_logger(__name__)

    if not isinstance(value, str):
        LOG.warning("Got object of type '%s' instead of str during profile serialization", type(value).__name__)
        return "[invalid string]%s" % type(value).__name__

    return value


# 3.10 and below (the noop version)
def _sanitize_string_identity(value):
    # type: (Any) -> str
    return value


# Assign based on version
sanitize_string = _sanitize_string_check if version_info[:2] > (3, 10) else _sanitize_string_identity
