import contextlib
import functools
import json
from typing import TYPE_CHECKING

from six import BytesIO
from werkzeug.exceptions import BadRequest
import xmltodict

from ddtrace import config
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._constants import WAF_CONTEXT_NAMES
from ddtrace.appsec.iast._input_info import Input_info
from ddtrace.appsec.iast._taint_tracking import taint_pyobject
from ddtrace.appsec.iast._taint_utils import LazyTaintDict
from ddtrace.appsec.iast._util import _is_iast_enabled
from ddtrace.internal import core
from ddtrace.internal.compat import parse
from ddtrace.internal.constants import HTTP_REQUEST_BLOCKED
from ddtrace.internal.constants import HTTP_REQUEST_COOKIE_NAME
from ddtrace.internal.constants import HTTP_REQUEST_COOKIE_VALUE
from ddtrace.internal.constants import HTTP_REQUEST_PATH
from ddtrace.internal.constants import HTTP_REQUEST_QUERY
from ddtrace.internal.logger import get_logger


try:
    from json import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore


if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import Generator
    from typing import List
    from typing import Optional
    from typing import Tuple


log = get_logger(__name__)

# Stopgap module for providing ASM context for the blocking features wrapping some contextvars.

_WAF_ADDRESSES = "waf_addresses"
_CALLBACKS = "callbacks"
_TELEMETRY = "telemetry"
_CONTEXT_CALL = "context"
_WAF_CALL = "waf_run"
_BLOCK_CALL = "block"
_WAF_RESULTS = "waf_results"
_BODY_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


GLOBAL_CALLBACKS = {}  # type: dict[str, Any]


class ASM_Environment:
    """
    an object of this class contains all asm data (waf and telemetry)
    for a single request. It is bound to a single asm request context.
    It is contained into a ContextVar.
    """

    def __init__(self, active=False):  # type: (bool) -> None
        self.active = active
        self.waf_addresses = {}  # type: dict[str, Any]
        self.callbacks = {}  # type: dict[str, Any]
        self.telemetry = {}  # type: dict[str, Any]
        self.addresses_sent = set()  # type: set[str]


def _get_asm_context():
    resources = core.root.get_item("resources")
    env = resources.asm_env
    if env is None:
        env = ASM_Environment()
        core.root.get_item("resources").asm_env = env
    return env


def free_context_available():  # type: () -> bool
    env = _get_asm_context()
    return env.active


def in_context():  # type: () -> bool
    env = _get_asm_context()
    return env.active


def is_blocked():  # type: () -> bool
    try:
        env = _get_asm_context()
        if not env.active:
            return False
        return bool(core.get_item(WAF_CONTEXT_NAMES.BLOCKED))
    except BaseException:
        return False


def register(span):
    _get_asm_context().span = span


def finalize(span):
    env = _get_asm_context()
    if env.span is span:
        env.span = None
    for function in GLOBAL_CALLBACKS.get(_CONTEXT_CALL, []):
        function(env)


class _DataHandler:
    """
    An object of this class is created by each asm request context.
    It handles the creation and destruction of ASM_Environment object.
    It allows the ASM context to be reentrant.
    """

    main_id = 0

    def __init__(self):
        _DataHandler.main_id += 1
        self.asm_env = ASM_Environment(True)

        self._id = _DataHandler.main_id
        self.active = True

        self.asm_env.telemetry[_WAF_RESULTS] = [], [], []
        self.asm_env.callbacks[_CONTEXT_CALL] = []

    def finalise(self):
        if self.active:
            env = self.asm_env
            # assert _CONTEXT_ID.get() == self._id
            callbacks = GLOBAL_CALLBACKS.get(_CONTEXT_CALL, []) + env.callbacks.get(_CONTEXT_CALL)
            if callbacks is not None:
                for function in callbacks:
                    function(env)
            self.active = False


def set_value(category, address, value):  # type: (str, str, Any) -> None
    env = _get_asm_context()
    if not env.active:
        log.debug("setting %s address %s with no active asm context", category, address)
        return
    asm_context_attr = getattr(env, category, None)
    if asm_context_attr is not None:
        asm_context_attr[address] = value


def set_headers_response(headers):  # type: (Any) -> None
    if headers is not None:
        set_waf_address(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, headers)


def set_body_response(body_response):
    # local import to avoid circular import
    from ddtrace.appsec.utils import parse_response_body

    parsed_body = parse_response_body(body_response)

    if parse_response_body is not None:
        set_waf_address(SPAN_DATA_NAMES.RESPONSE_BODY, parsed_body)


def set_waf_address(address, value):  # type: (str, Any, Any) -> None
    if address == SPAN_DATA_NAMES.REQUEST_URI_RAW:
        parse_address = parse.urlparse(value)
        no_scheme = parse.ParseResult("", "", *parse_address[2:])
        waf_value = parse.urlunparse(no_scheme)
        set_value(_WAF_ADDRESSES, address, waf_value)
    else:
        set_value(_WAF_ADDRESSES, address, value)
    core.set_item(address, value)


def get_value(category, address, default=None):  # type: (str, str, Any) -> Any
    env = _get_asm_context()
    if not env.active:
        log.debug("getting %s address %s with no active asm context", category, address)
        return default
    asm_context_attr = getattr(env, category, None)
    if asm_context_attr is not None:
        return asm_context_attr.get(address, default)
    return default


def get_waf_address(address, default=None):  # type: (str, Any) -> Any
    return get_value(_WAF_ADDRESSES, address, default=default)


def get_waf_addresses(default=None):  # type: (Any) -> Any
    env = _get_asm_context()
    if not env.active:
        log.debug("getting WAF addresses with no active asm context")
        return default
    return env.waf_addresses


def add_context_callback(function, global_callback=False):  # type: (Any, bool) -> None
    if global_callback:
        callbacks = GLOBAL_CALLBACKS.setdefault(_CONTEXT_CALL, [])
    else:
        callbacks = get_value(_CALLBACKS, _CONTEXT_CALL)
    if callbacks is not None:
        callbacks.append(function)


def remove_context_callback(function, global_callback=False):  # type: (Any, bool) -> None
    if global_callback:
        callbacks = GLOBAL_CALLBACKS.get(_CONTEXT_CALL)
    else:
        callbacks = get_value(_CALLBACKS, _CONTEXT_CALL)
    if callbacks:
        callbacks[:] = list([cb for cb in callbacks if cb != function])


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
        set_waf_address(SPAN_DATA_NAMES.REQUEST_HTTP_IP, ip)


def get_ip():  # type: () -> Optional[str]
    return get_value(_WAF_ADDRESSES, SPAN_DATA_NAMES.REQUEST_HTTP_IP)


# Note: get/set headers use Any since we just carry the headers here without changing or using them
# and different frameworks use different types that we don't want to force it into a Mapping at the
# early point set_headers is usually called


def set_headers(headers):  # type: (Any) -> None
    if headers is not None:
        set_waf_address(SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, headers)


def get_headers():  # type: () -> Optional[Any]
    return get_value(_WAF_ADDRESSES, SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, {})


def set_headers_case_sensitive(case_sensitive):  # type: (bool) -> None
    set_waf_address(SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES_CASE, case_sensitive)


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
    env = _get_asm_context()
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
        resources = _on_context_started()
        try:
            yield resources
        finally:
            _on_context_ended()
    else:
        yield None


def _on_block_decided(callback):
    set_value(_CALLBACKS, "flask_block", callback)


def _on_pre_tracedrequest(block_request_callable, span):
    if config._appsec_enabled:
        set_block_request_callable(functools.partial(block_request_callable, span))
        if core.get_item(HTTP_REQUEST_BLOCKED):
            block_request()


def _on_post_finalizerequest(rv):
    if config._api_security_enabled and config._appsec_enabled and getattr(rv, "is_sequence", False):
        # start_response was not called yet, set the HTTP response headers earlier
        set_headers_response(list(rv.headers))
        set_body_response(rv.response)


def _on_startresponse():
    log.debug("Flask WAF call for Suspicious Request Blocking on response")
    call_waf_callback()
    return get_headers().get("Accept", "").lower()


def _on_request_spanmodifier(request, environ, _HAS_JSON_MIXIN):
    req_body = None
    if config._appsec_enabled and request.method in _BODY_METHODS:
        content_type = request.content_type
        wsgi_input = environ.get("wsgi.input", "")

        # Copy wsgi input if not seekable
        if wsgi_input:
            try:
                seekable = wsgi_input.seekable()
            except AttributeError:
                seekable = False
            if not seekable:
                content_length = int(environ.get("CONTENT_LENGTH", 0))
                body = wsgi_input.read(content_length) if content_length else wsgi_input.read()
                environ["wsgi.input"] = BytesIO(body)

        try:
            if content_type == "application/json" or content_type == "text/json":
                if _HAS_JSON_MIXIN and hasattr(request, "json") and request.json:
                    req_body = request.json
                else:
                    req_body = json.loads(request.data.decode("UTF-8"))
            elif content_type in ("application/xml", "text/xml"):
                req_body = xmltodict.parse(request.get_data())
            elif hasattr(request, "form"):
                req_body = request.form.to_dict()
            else:
                # no raw body
                req_body = None
        except (
            AttributeError,
            RuntimeError,
            TypeError,
            BadRequest,
            ValueError,
            JSONDecodeError,
            xmltodict.expat.ExpatError,
            xmltodict.ParsingInterrupted,
        ):
            log.warning("Failed to parse werkzeug request body", exc_info=True)
        finally:
            # Reset wsgi input to the beginning
            if wsgi_input:
                if seekable:
                    wsgi_input.seek(0)
                else:
                    environ["wsgi.input"] = BytesIO(body)
    return req_body


def _on_set_request_tags(request):
    if _is_iast_enabled():
        return LazyTaintDict(
            request.cookies,
            origins=(HTTP_REQUEST_COOKIE_NAME, HTTP_REQUEST_COOKIE_VALUE),
            override_pyobject_tainted=True,
        )


def _on_request_init(instance):
    if _is_iast_enabled():
        try:

            taint_pyobject(
                instance.query_string,
                Input_info(HTTP_REQUEST_QUERY, instance.query_string, HTTP_REQUEST_QUERY),
            )
            taint_pyobject(instance.path, Input_info(HTTP_REQUEST_PATH, instance.path, HTTP_REQUEST_PATH))
        except Exception:
            log.debug("Unexpected exception while tainting pyobject", exc_info=True)


def _on_context_started(context=None):
    if context is None:
        context = core._CURRENT_CONTEXT.get()
    resources = _DataHandler()
    context.root().set_item("resources", resources)
    asm_request_context_set(
        context.get_item("remote_addr"),
        context.get_item("headers"),
        context.get_item("headers_case_sensitive"),
        context.get_item("block_request_callable"),
    )
    core.on("flask.start_response", _on_startresponse)
    core.on("flask.request_init", _on_request_init)
    core.on("flask.traced_request.pre", _on_pre_tracedrequest)
    core.on("flask.finalize_request.post", _on_post_finalizerequest)
    core.on("flask.request_span_modifier", _on_request_spanmodifier)
    core.on("flask.set_request_tags", _on_set_request_tags)
    core.on("wsgi.block_decided", _on_block_decided)
    return resources


def _on_context_ended(context):
    context.root().get_item("resources").finalise()


core.on("context.started.wsgi.__call__", _on_context_started)
core.on("context.ended.wsgi.__call__", _on_context_ended)
