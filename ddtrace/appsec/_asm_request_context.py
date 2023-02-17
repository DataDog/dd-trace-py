import contextlib
from typing import Any
from typing import Generator
from typing import Optional

from ddtrace.vendor import contextvars


"""
Stopgap module for providing ASM context for the blocking features wrapping some
contextvars. When using this, note that context vars are always thread-local so each
thread will have a different context.
"""


# FIXME: remove these and use the new context API once implemented and allowing
# contexts without spans

_DD_EARLY_IP_CONTEXTVAR = contextvars.ContextVar("datadog_early_ip_contextvar", default=None)
_DD_EARLY_HEADERS_CONTEXTVAR = contextvars.ContextVar("datadog_early_headers_contextvar", default=None)
_DD_EARLY_HEADERS_CASE_SENSITIVE_CONTEXTVAR = contextvars.ContextVar(
    "datadog_early_headers_casesensitive_contextvar", default=False
)
_DD_WAF_CALLBACK = contextvars.ContextVar("datadog_early_waf_callback", default=None)


def reset():  # type: () -> None
    _DD_EARLY_IP_CONTEXTVAR.set(None)
    _DD_EARLY_HEADERS_CONTEXTVAR.set(None)
    _DD_EARLY_HEADERS_CASE_SENSITIVE_CONTEXTVAR.set(False)


def set_ip(ip):  # type: (Optional[str]) -> None
    _DD_EARLY_IP_CONTEXTVAR.set(ip)


def get_ip():  # type: () -> Optional[str]
    return _DD_EARLY_IP_CONTEXTVAR.get()


# Note: get/set headers use Any since we just carry the headers here without changing or using them
# and different frameworks use different types that we don't want to force it into a Mapping at the
# early point set_headers is usually called


def set_headers(headers):  # type: (Any) -> None
    _DD_EARLY_HEADERS_CONTEXTVAR.set(headers)


def get_headers():  # type: () -> Optional[Any]
    return _DD_EARLY_HEADERS_CONTEXTVAR.get()


def set_headers_case_sensitive(case_sensitive):  # type: (bool) -> None
    _DD_EARLY_HEADERS_CASE_SENSITIVE_CONTEXTVAR.set(case_sensitive)


def get_headers_case_sensitive():  # type: () -> bool
    return _DD_EARLY_HEADERS_CASE_SENSITIVE_CONTEXTVAR.get()


def set_waf_callback(callback):  # type: (Any) -> None
    _DD_WAF_CALLBACK.set(callback)


def get_waf_callback():  # type: () -> Any
    return _DD_WAF_CALLBACK.get()


def asm_request_context_set(remote_ip=None, headers=None, headers_case_sensitive=False):
    # type: (Optional[str], Any, bool) -> None
    set_ip(remote_ip)
    set_headers(headers)
    set_headers_case_sensitive(headers_case_sensitive)


@contextlib.contextmanager
def asm_request_context_manager(remote_ip=None, headers=None, headers_case_sensitive=False):
    # type: (Optional[str], Any, bool) -> Generator[None, None, None]
    asm_request_context_set(remote_ip, headers, headers_case_sensitive)
    try:
        yield
    finally:
        reset()
