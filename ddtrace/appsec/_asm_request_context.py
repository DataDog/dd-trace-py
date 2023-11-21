import contextlib
import functools
from typing import Any
from typing import Callable
from typing import Dict
from typing import Generator
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from urllib import parse

from ddtrace import config
from ddtrace.appsec import _handlers
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._constants import WAF_CONTEXT_NAMES
from ddtrace.appsec._iast._utils import _is_iast_enabled
from ddtrace.internal import core
from ddtrace.internal.constants import REQUEST_PATH_PARAMS
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config
from ddtrace.span import Span


log = get_logger(__name__)

# Stopgap module for providing ASM context for the blocking features wrapping some contextvars.

_WAF_ADDRESSES = "waf_addresses"
_CALLBACKS = "callbacks"
_TELEMETRY = "telemetry"
_CONTEXT_CALL = "context"
_WAF_CALL = "waf_run"
_BLOCK_CALL = "block"
_WAF_RESULTS = "waf_results"


GLOBAL_CALLBACKS: Dict[str, Any] = {}


class ASM_Environment:
    """
    an object of this class contains all asm data (waf and telemetry)
    for a single request. It is bound to a single asm request context.
    It is contained into a ContextVar.
    """

    def __init__(self, active: bool = False):
        self.active: bool = active
        self.span: Optional[Span] = None
        self.span_asm_context: Optional[contextlib.AbstractContextManager] = None
        self.waf_addresses: Dict[str, Any] = {}
        self.callbacks: Dict[str, Any] = {}
        self.telemetry: Dict[str, Any] = {}
        self.addresses_sent: Set[str] = set()
        self.must_call_globals: bool = True


def _get_asm_context() -> ASM_Environment:
    env = core.get_item("asm_env")
    if env is None:
        env = ASM_Environment()
        core.set_item("asm_env", env)
    return env


def free_context_available() -> bool:
    env = _get_asm_context()
    return env.active and env.span is None


def in_context() -> bool:
    env = _get_asm_context()
    return env.active


def is_blocked() -> bool:
    try:
        env = _get_asm_context()
        if not env.active or env.span is None:
            return False
        return bool(core.get_item(WAF_CONTEXT_NAMES.BLOCKED, span=env.span))
    except BaseException:
        return False


def register(span: Span, span_asm_context=None) -> None:
    env = _get_asm_context()
    if not env.active:
        log.debug("registering a span with no active asm context")
        return
    env.span = span
    env.span_asm_context = span_asm_context


def unregister(span: Span) -> None:
    env = _get_asm_context()
    if env.span_asm_context is not None and env.span is span:
        env.span_asm_context.__exit__(None, None, None)
    elif env.span is span and env.must_call_globals:
        # needed for api security flushing information before end of the span
        for function in GLOBAL_CALLBACKS.get(_CONTEXT_CALL, []):
            function(env)
        env.must_call_globals = False


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
        self.execution_context = core.ExecutionContext(__name__, **{"asm_env": env})

        env.telemetry[_WAF_RESULTS] = [], [], []
        env.callbacks[_CONTEXT_CALL] = []

    def finalise(self):
        if self.active:
            env = self.execution_context.get_item("asm_env")
            callbacks = GLOBAL_CALLBACKS.get(_CONTEXT_CALL, []) if env.must_call_globals else []
            env.must_call_globals = False
            if env is not None and env.callbacks is not None and env.callbacks.get(_CONTEXT_CALL):
                callbacks += env.callbacks.get(_CONTEXT_CALL)
            if callbacks:
                if env is not None:
                    for function in callbacks:
                        function(env)
                self.execution_context.end()
            self.active = False


def set_value(category: str, address: str, value: Any) -> None:
    env = _get_asm_context()
    if not env.active:
        log.debug("setting %s address %s with no active asm context", category, address)
        return
    asm_context_attr = getattr(env, category, None)
    if asm_context_attr is not None:
        asm_context_attr[address] = value


def set_headers_response(headers: Any) -> None:
    if headers is not None:
        set_waf_address(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, headers, _get_asm_context().span)


def set_body_response(body_response):
    # local import to avoid circular import
    from ddtrace.appsec._utils import parse_response_body

    parsed_body = parse_response_body(body_response)

    if parse_response_body is not None:
        set_waf_address(SPAN_DATA_NAMES.RESPONSE_BODY, parsed_body)


def set_waf_address(address: str, value: Any, span: Optional[Span] = None) -> None:
    if address == SPAN_DATA_NAMES.REQUEST_URI_RAW:
        parse_address = parse.urlparse(value)
        no_scheme = parse.ParseResult("", "", *parse_address[2:])
        waf_value = parse.urlunparse(no_scheme)
        set_value(_WAF_ADDRESSES, address, waf_value)
    else:
        set_value(_WAF_ADDRESSES, address, value)
    if span is None:
        span = _get_asm_context().span
    if span:
        core.set_item(address, value, span=span)


def get_value(category: str, address: str, default: Any = None) -> Any:
    env = _get_asm_context()
    if not env.active:
        log.debug("getting %s address %s with no active asm context", category, address)
        return default
    asm_context_attr = getattr(env, category, None)
    if asm_context_attr is not None:
        return asm_context_attr.get(address, default)
    return default


def get_waf_address(address: str, default: Any = None) -> Any:
    return get_value(_WAF_ADDRESSES, address, default=default)


def get_waf_addresses(default: Any = None) -> Any:
    env = _get_asm_context()
    if not env.active:
        log.debug("getting WAF addresses with no active asm context")
        return default
    return env.waf_addresses


def add_context_callback(function, global_callback: bool = False) -> None:
    if global_callback:
        callbacks = GLOBAL_CALLBACKS.setdefault(_CONTEXT_CALL, [])
    else:
        callbacks = get_value(_CALLBACKS, _CONTEXT_CALL)
    if callbacks is not None:
        callbacks.append(function)


def remove_context_callback(function, global_callback: bool = False) -> None:
    if global_callback:
        callbacks = GLOBAL_CALLBACKS.get(_CONTEXT_CALL)
    else:
        callbacks = get_value(_CALLBACKS, _CONTEXT_CALL)
    if callbacks:
        callbacks[:] = list([cb for cb in callbacks if cb != function])


def set_waf_callback(value) -> None:
    set_value(_CALLBACKS, _WAF_CALL, value)


def call_waf_callback(custom_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, str]]:
    if not asm_config._asm_enabled:
        return None
    callback = get_value(_CALLBACKS, _WAF_CALL)
    if callback:
        return callback(custom_data)
    else:
        log.warning("WAF callback called but not set")
        return None


def set_ip(ip: Optional[str]) -> None:
    if ip is not None:
        set_waf_address(SPAN_DATA_NAMES.REQUEST_HTTP_IP, ip, _get_asm_context().span)


def get_ip() -> Optional[str]:
    return get_value(_WAF_ADDRESSES, SPAN_DATA_NAMES.REQUEST_HTTP_IP)


# Note: get/set headers use Any since we just carry the headers here without changing or using them
# and different frameworks use different types that we don't want to force it into a Mapping at the
# early point set_headers is usually called


def set_headers(headers: Any) -> None:
    if headers is not None:
        set_waf_address(SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, headers, _get_asm_context().span)


def get_headers() -> Optional[Any]:
    return get_value(_WAF_ADDRESSES, SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, {})


def set_headers_case_sensitive(case_sensitive: bool) -> None:
    set_waf_address(SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES_CASE, case_sensitive, _get_asm_context().span)


def get_headers_case_sensitive() -> bool:
    return get_value(_WAF_ADDRESSES, SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES_CASE, False)  # type : ignore


def set_block_request_callable(_callable: Optional[Callable], *_) -> None:
    """
    Sets a callable that could be use to do a best-effort to block the request. If
    the callable need any params, like headers, they should be curried with
    functools.partial.
    """
    if _callable:
        set_value(_CALLBACKS, _BLOCK_CALL, _callable)


def block_request() -> None:
    """
    Calls or returns the stored block request callable, if set.
    """
    _callable = get_value(_CALLBACKS, _BLOCK_CALL)
    if _callable:
        _callable()
    else:
        log.debug("Block request called but block callable not set by framework")


def get_data_sent() -> Set[str]:
    env = _get_asm_context()
    if not env.active:
        log.debug("getting addresses sent with no active asm context")
        return set()
    return env.addresses_sent


def asm_request_context_set(
    remote_ip: Optional[str] = None,
    headers: Any = None,
    headers_case_sensitive: bool = False,
    block_request_callable: Optional[Callable] = None,
) -> None:
    set_ip(remote_ip)
    set_headers(headers)
    set_headers_case_sensitive(headers_case_sensitive)
    set_block_request_callable(block_request_callable)


def set_waf_results(result_data, result_info, is_blocked) -> None:
    three_lists = get_waf_results()
    if three_lists is not None:
        list_results_data, list_result_info, list_is_blocked = three_lists
        list_results_data.append(result_data)
        list_result_info.append(result_info)
        list_is_blocked.append(is_blocked)


def get_waf_results() -> Optional[Tuple[List[Any], List[Any], List[bool]]]:
    return get_value(_TELEMETRY, _WAF_RESULTS)


def reset_waf_results() -> None:
    set_value(_TELEMETRY, _WAF_RESULTS, ([], [], []))


@contextlib.contextmanager
def asm_request_context_manager(
    remote_ip: Optional[str] = None,
    headers: Any = None,
    headers_case_sensitive: bool = False,
    block_request_callable: Optional[Callable] = None,
) -> Generator[Optional[_DataHandler], None, None]:
    """
    The ASM context manager
    """
    resources = _start_context(remote_ip, headers, headers_case_sensitive, block_request_callable)
    if resources is not None:
        try:
            yield resources
        finally:
            _end_context(resources)
    else:
        yield None


def _start_context(
    remote_ip: Optional[str], headers: Any, headers_case_sensitive: bool, block_request_callable: Optional[Callable]
) -> Optional[_DataHandler]:
    if asm_config._asm_enabled:
        resources = _DataHandler()
        asm_request_context_set(remote_ip, headers, headers_case_sensitive, block_request_callable)
        _handlers.listen()
        listen_context_handlers()
        return resources
    return None


def _on_context_started(ctx):
    resources = _start_context(
        ctx.get_item("remote_addr"),
        ctx.get_item("headers"),
        ctx.get_item("headers_case_sensitive"),
        ctx.get_item("block_request_callable"),
    )
    ctx.set_item("resources", resources)


def _end_context(resources):
    resources.finalise()
    core.set_item("asm_env", None)


def _on_context_ended(ctx):
    resources = ctx.get_item("resources")
    if resources is not None:
        _end_context(resources)


core.on("context.started.wsgi.__call__", _on_context_started)
core.on("context.ended.wsgi.__call__", _on_context_ended)
core.on("context.started.django.traced_get_response", _on_context_started)
core.on("context.ended.django.traced_get_response", _on_context_ended)
core.on("django.traced_get_response.pre", set_block_request_callable)


def _on_wrapped_view(kwargs):
    return_value = [None, None]
    # if Appsec is enabled, we can try to block as we have the path parameters at that point
    if asm_config._asm_enabled and in_context():
        log.debug("Flask WAF call for Suspicious Request Blocking on request")
        if kwargs:
            set_waf_address(REQUEST_PATH_PARAMS, kwargs)
        call_waf_callback()
        if is_blocked():
            callback_block = get_value(_CALLBACKS, "flask_block")
            return_value[0] = callback_block

    # If IAST is enabled, taint the Flask function kwargs (path parameters)
    if _is_iast_enabled() and kwargs:
        from ddtrace.appsec._iast._taint_tracking import OriginType
        from ddtrace.appsec._iast._taint_tracking import taint_pyobject

        _kwargs = {}
        for k, v in kwargs.items():
            _kwargs[k] = taint_pyobject(
                pyobject=v, source_name=k, source_value=v, source_origin=OriginType.PATH_PARAMETER
            )
        return_value[1] = _kwargs
    return return_value


def _on_set_request_tags(request, span, flask_config):
    if _is_iast_enabled():
        from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_source
        from ddtrace.appsec._iast._taint_tracking import OriginType
        from ddtrace.appsec._iast._taint_utils import LazyTaintDict

        _set_metric_iast_instrumented_source(OriginType.COOKIE_NAME)
        _set_metric_iast_instrumented_source(OriginType.COOKIE)

        request.cookies = LazyTaintDict(
            request.cookies,
            origins=(OriginType.COOKIE_NAME, OriginType.COOKIE),
            override_pyobject_tainted=True,
        )


def _on_pre_tracedrequest(ctx):
    _on_set_request_tags(ctx.get_item("flask_request"), ctx["current_span"], ctx.get_item("flask_config"))
    block_request_callable = ctx.get_item("block_request_callable")
    current_span = ctx["current_span"]
    if asm_config._asm_enabled:
        set_block_request_callable(functools.partial(block_request_callable, current_span))
        if core.get_item(WAF_CONTEXT_NAMES.BLOCKED):
            block_request()


def _set_headers_and_response(response, headers, *_):
    from ddtrace.appsec._utils import _appsec_apisec_features_is_active

    if _appsec_apisec_features_is_active():
        if headers:
            # start_response was not called yet, set the HTTP response headers earlier
            set_headers_response(list(headers))
        if response:
            set_body_response(response)


def _call_waf_first(integration, *_):
    log.debug("%s WAF call for Suspicious Request Blocking on request", integration)
    call_waf_callback()


def _call_waf(integration, *_):
    log.debug("%s WAF call for Suspicious Request Blocking on response", integration)
    call_waf_callback()


def _on_block_decided(callback):
    set_value(_CALLBACKS, "flask_block", callback)


def _get_headers_if_appsec():
    if asm_config._asm_enabled:
        return get_headers()


def listen_context_handlers():
    core.on("flask.finalize_request.post", _set_headers_and_response)
    core.on("flask.wrapped_view", _on_wrapped_view)
    core.on("flask._patched_request", _on_pre_tracedrequest)
    core.on("wsgi.block_decided", _on_block_decided)
    core.on("flask.start_response", _call_waf)

    core.on("django.start_response.post", _call_waf)
    core.on("django.finalize_response", _call_waf)
    core.on("django.after_request_headers", _get_headers_if_appsec)
    core.on("django.extract_body", _get_headers_if_appsec)
    core.on("django.after_request_headers.finalize", _set_headers_and_response)
    core.on("flask.set_request_tags", _on_set_request_tags)

    core.on("asgi.start_request", _call_waf_first)
    core.on("asgi.start_response", _call_waf)
