import contextlib
from typing import TYPE_CHECKING

from ddtrace import config
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.internal import _context
from ddtrace.internal.logger import get_logger


try:
    import contextvars
except ImportError:
    import ddtrace.vendor.contextvars as contextvars  # type: ignore


if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import Generator
    from typing import List
    from typing import Optional
    from typing import Tuple


log = get_logger(__name__)

"""
Stopgap module for providing ASM context for the blocking features wrapping some
contextvars. When using this, note that context vars are always thread-local so each
thread will have a different context.
"""

# FIXME: remove these and use the new context API once implemented and allowing
# contexts without spans


_WAF_ADDRESSES = "waf_addresses"
_CALLBACKS = "callbacks"
_TELEMETRY = "telemetry"
_CONTEXT_CALL = "context"
_WAF_CALL = "waf_run"
_BLOCK_CALL = "block"
_WAF_RESULTS = "waf_results"


class ASM_Environment:
    """
    an object of this class contains all asm data (waf and telemetry)
    for a single request. It is bound to a single asm request context.
    It is contained into a ContextVar.
    """

    def __init__(self, active=False):  # type: (bool) -> None
        self.active = active
        self.span = None
        self.waf_addresses = {}  # type: dict[str, Any]
        self.callbacks = {}  # type: dict[str, Any]
        self.telemetry = {}  # type: dict[str, Any]
        self.addresses_sent = set()  # type: set[str]


_ASM = contextvars.ContextVar("ASM_contextvar", default=ASM_Environment())


def free_context_available():  # type: () -> bool
    env = _ASM.get()
    return env.active and env.span is None


def in_context():  # type: () -> bool
    env = _ASM.get()
    return env.active


def is_blocked():  # type: () -> bool
    try:
        env = _ASM.get()
        if not env.active or env.span is None:
            return False
        return _context.get_item("http.request.blocked", span=env.span)
    except BaseException:
        return False


def register(span):
    env = _ASM.get()
    if not env.active:
        log.debug("registering a span with no active asm context")
        return
    env.span = span


class _DataHandler:
    """
    An object of this class is created by each asm request context.
    It handles the creation and destruction of ASM_Environment object.
    It allows the ASM context to be reentrant.
    """

    main_id = 0

    def __init__(self):
        _DataHandler.main_id += 1
        env = ASM_Environment(True)

        self._id = _DataHandler.main_id
        self.active = True
        self.token = _ASM.set(env)

        env.telemetry[_WAF_RESULTS] = [], [], []
        env.callbacks[_CONTEXT_CALL] = []

    def finalise(self):
        if self.active:
            env = _ASM.get()
            # assert _CONTEXT_ID.get() == self._id
            callbacks = env.callbacks.get(_CONTEXT_CALL)
            if callbacks is not None:
                for function in callbacks:
                    function()
                _ASM.reset(self.token)
            self.active = False


def set_value(category, address, value):  # type: (str, str, Any) -> None
    env = _ASM.get()
    if not env.active:
        log.debug("setting %s address %s with no active asm context", category, address)
        return
    asm_context_attr = getattr(env, category, None)
    if asm_context_attr is not None:
        asm_context_attr[address] = value


def set_waf_address(address, value, span=None):  # type: (str, Any, Any) -> None
    set_value(_WAF_ADDRESSES, address, value)
    if span is None:
        span = _ASM.get().span
    if span:
        _context.set_item(address, value, span=span)


def get_value(category, address, default=None):  # type: (str, str, Any) -> Any
    env = _ASM.get()
    if not env.active:
        log.debug("getting %s address %s with no active asm context", category, address)
        return default
    asm_context_attr = getattr(env, category, None)
    if asm_context_attr is not None:
        return asm_context_attr.get(address, default)
    return default


def get_waf_address(address, default=None):  # type: (str, Any) -> Any
    return get_value(_WAF_ADDRESSES, address, default=default)


def add_context_callback(function):  # type: (Any) -> None
    callbacks = get_value(_CALLBACKS, _CONTEXT_CALL)
    if callbacks is not None:
        callbacks.append(function)


def set_waf_callback(value):  # type: (Any) -> None
    set_value(_CALLBACKS, _WAF_CALL, value)


def call_waf_callback(custom_data=None):
    # type: (dict[str, Any] | None) -> None
    if not config._appsec_enabled:
        return
    callback = get_value(_CALLBACKS, _WAF_CALL)
    if callback:
        return callback(custom_data)
    else:
        log.warning("WAF callback called but not set")


def set_ip(ip):  # type: (Optional[str]) -> None
    if ip is not None:
        set_waf_address(SPAN_DATA_NAMES.REQUEST_HTTP_IP, ip, _ASM.get().span)


def get_ip():  # type: () -> Optional[str]
    return get_value(_WAF_ADDRESSES, SPAN_DATA_NAMES.REQUEST_HTTP_IP)


# Note: get/set headers use Any since we just carry the headers here without changing or using them
# and different frameworks use different types that we don't want to force it into a Mapping at the
# early point set_headers is usually called


def set_headers(headers):  # type: (Any) -> None
    if headers is not None:
        set_waf_address(SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, headers, _ASM.get().span)


def get_headers():  # type: () -> Optional[Any]
    return get_value(_WAF_ADDRESSES, SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, {})


def set_headers_case_sensitive(case_sensitive):  # type: (bool) -> None
    set_waf_address(SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES_CASE, case_sensitive, _ASM.get().span)


def get_headers_case_sensitive():  # type: () -> bool
    return get_value(_WAF_ADDRESSES, SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES_CASE, False)  # type : ignore


def set_block_request_callable(_callable):  # type: (Optional[Callable]) -> None
    """
    Sets a callable that could be use to do a best-effort to block the request. If
    the callable need any params, like headers, they should be curried with
    functools.partial.
    """
    if _callable:
        set_value(_CALLBACKS, _BLOCK_CALL, _callable)


def block_request():  # type: () -> None
    """
    Calls or returns the stored block request callable, if set.
    """
    _callable = get_value(_CALLBACKS, _BLOCK_CALL)
    if _callable:
        _callable()
    else:
        log.debug("Block request called but block callable not set by framework")


def get_data_sent():  # type: () -> set[str] | None
    env = _ASM.get()
    if not env.active:
        log.debug("getting addresses sent with no active asm context")
        return set()
    return env.addresses_sent


def asm_request_context_set(remote_ip=None, headers=None, headers_case_sensitive=False, block_request_callable=None):
    # type: (Optional[str], Any, bool, Optional[Callable]) -> None
    set_ip(remote_ip)
    set_headers(headers)
    set_headers_case_sensitive(headers_case_sensitive)
    set_block_request_callable(block_request_callable)


def set_waf_results(result_data, result_info, is_blocked):  # type: (Any, Any, bool) -> None
    three_lists = get_waf_results()
    if three_lists is not None:
        list_results_data, list_result_info, list_is_blocked = three_lists
        list_results_data.append(result_data)
        list_result_info.append(result_info)
        list_is_blocked.append(is_blocked)


def get_waf_results():  # type: () -> Tuple[List[Any], List[Any], List[bool]] | None
    return get_value(_TELEMETRY, _WAF_RESULTS)


def reset_waf_results():  # type: () -> None
    set_value(_TELEMETRY, _WAF_RESULTS, ([], [], []))


@contextlib.contextmanager
def asm_request_context_manager(
    remote_ip=None, headers=None, headers_case_sensitive=False, block_request_callable=None
):
    # type: (Optional[str], Any, bool, Optional[Callable]) -> Generator[_DataHandler|None, None, None]
    """
    The ASM context manager
    """
    if config._appsec_enabled:
        resources = _DataHandler()
        asm_request_context_set(remote_ip, headers, headers_case_sensitive, block_request_callable)
        try:
            yield resources
        finally:
            resources.finalise()
    else:
        yield None
