import contextlib
from typing import TYPE_CHECKING

from ddtrace import config
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import Generator
    from typing import List
    from typing import Optional
    from typing import Tuple

from ddtrace.vendor import contextvars


log = get_logger(__name__)

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
_DD_BLOCK_REQUEST_CALLABLE = contextvars.ContextVar("datadog_block_request_callable_contextvar", default=None)
_DD_WAF_CALLBACK = contextvars.ContextVar("datadog_early_waf_callback", default=None)
_DD_WAF_RESULTS = contextvars.ContextVar("datadog_early_waf_results", default=([[], [], []]))


def reset():  # type: () -> None
    _DD_EARLY_IP_CONTEXTVAR.set(None)
    _DD_EARLY_HEADERS_CONTEXTVAR.set(None)
    _DD_EARLY_HEADERS_CASE_SENSITIVE_CONTEXTVAR.set(False)
    _DD_BLOCK_REQUEST_CALLABLE.set(None)


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


def set_block_request_callable(_callable):  # type: (Optional[Callable]) -> None
    """
    Sets a callable that could be use to do a best-effort to block the request. If
    the callable need any params, like headers, they should be curried with
    functools.partial.
    """
    if _callable:
        _DD_BLOCK_REQUEST_CALLABLE.set(_callable)


def block_request():  # type: () -> None
    """
    Calls or returns the stored block request callable, if set.
    """
    _callable = _DD_BLOCK_REQUEST_CALLABLE.get()
    if _callable:
        _callable()

    log.debug("Block request called but block callable not set by framework")


def set_waf_callback(callback):  # type: (Any) -> None
    _DD_WAF_CALLBACK.set(callback)


def call_waf_callback(custom_data=None):
    # type: (dict[str, Any] | None) -> None
    if not config._appsec_enabled:
        return
    callback = _DD_WAF_CALLBACK.get()
    if callback:
        return callback(custom_data)
    else:
        log.warning("WAF callback called but not set")


def asm_request_context_set(remote_ip=None, headers=None, headers_case_sensitive=False, block_request_callable=None):
    # type: (Optional[str], Any, bool, Optional[Callable]) -> None
    set_ip(remote_ip)
    set_headers(headers)
    set_headers_case_sensitive(headers_case_sensitive)
    set_block_request_callable(block_request_callable)


def set_waf_results(result_data, result_info, is_blocked):  # type: (Any, Any, bool) -> None
    list_results_data, list_result_info, list_is_blocked = get_waf_results()
    list_results_data.append(result_data)
    list_result_info.append(result_info)
    list_is_blocked.append(is_blocked)
    _DD_WAF_RESULTS.set((list_results_data, list_result_info, list_is_blocked))


def get_waf_results():  # type: () -> Tuple[List[Any], List[Any], List[bool]]
    return _DD_WAF_RESULTS.get()


def reset_waf_results():  # type: () -> None
    _DD_WAF_RESULTS.set([[], [], []])


@contextlib.contextmanager
def asm_request_context_manager(
    remote_ip=None, headers=None, headers_case_sensitive=False, block_request_callable=None
):
    # type: (Optional[str], Any, bool, Optional[Callable]) -> Generator[None, None, None]
    asm_request_context_set(remote_ip, headers, headers_case_sensitive, block_request_callable)
    try:
        yield
    finally:
        reset()
