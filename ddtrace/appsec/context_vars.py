from typing import Any
from typing import Optional

from ddtrace.vendor import contextvars


# FIXME: remove these and use the new context API once implemented and allowing
# contexts without spans

_DD_EARLY_IP_CONTEXTVAR = contextvars.ContextVar(
    "datadog_early_ip_contextvar", default=None
)

_DD_EARLY_HEADERS_CONTEXTVAR = contextvars.ContextVar(
    "datadog_early_headers_contextvar", default=None
)

_DD_EARLY_HEADERS_CASE_SENSITIVE_CONTEXTVAR = contextvars.ContextVar(
    "datadog_early_headers_casesensitive_contextvar", default=False
)


def _reset_contextvars():
    _DD_EARLY_IP_CONTEXTVAR.set(None)
    _DD_EARLY_HEADERS_CONTEXTVAR.set(None)
    _DD_EARLY_HEADERS_CASE_SENSITIVE_CONTEXTVAR.set(False)
