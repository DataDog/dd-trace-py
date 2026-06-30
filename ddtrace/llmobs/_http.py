from ddtrace.internal.http import HTTPConnection
from ddtrace.internal.utils.http import DEFAULT_TIMEOUT
from ddtrace.internal.utils.http import get_connection
from ddtrace.internal.utils.http import verify_url


__all__ = ["DEFAULT_TIMEOUT", "HTTPConnection", "get_connection", "verify_url"]
