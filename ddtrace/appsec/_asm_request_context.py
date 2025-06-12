import functools
import json
import re
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing import Set
from typing import Union
from urllib import parse

from ddtrace._trace.span import Span
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._constants import Constant_Class
from ddtrace.appsec._utils import DDWaf_info
from ddtrace.appsec._utils import DDWaf_result
from ddtrace.appsec._utils import Telemetry_result
from ddtrace.appsec._utils import get_triggers
from ddtrace.contrib.internal.trace_utils_base import _normalize_tag_name
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.constants import REQUEST_PATH_PARAMS
import ddtrace.internal.logger as ddlogger
from ddtrace.settings.asm import config as asm_config


logger = ddlogger.get_logger(__name__)


class WARNING_TAGS(metaclass=Constant_Class):
    ASM_ENV_NO_SPAN = "asm_context::ASM_Environment::no_span"
    SET_BLOCKED_NO_ASM_CONTEXT = "asm_context::set_blocked::no_active_context"
    SET_WAF_INFO_NO_ASM_CONTEXT = "asm_context::set_waf_info::no_active_context"
    CALL_WAF_CALLBACK_NOT_SET = "asm_context::call_waf_callback::not_set"
    BLOCK_REQUEST_NOT_CALLABLE = "asm_context::block_request::not_callable"
    GET_DATA_SENT_NO_ASM_CONTEXT = "asm_context::get_data_sent::no_active_context"
    STORE_WAF_RESULTS_NO_ASM_CONTEXT = "asm_context::store_waf_results_data::no_active_context"


# limit warnings to one per day per process
for _, tag in WARNING_TAGS:
    ddlogger.set_tag_rate_limit(tag, ddlogger.DAY)

log_extra = {"product": "appsec", "stack_limit": 4, "exec_limit": 4}


# Stopgap module for providing ASM context for the blocking features wrapping some contextvars.


_ASM_CONTEXT: Literal["_asm_env"] = "_asm_env"
_WAF_ADDRESSES: Literal["waf_addresses"] = "waf_addresses"
_CALLBACKS: Literal["callbacks"] = "callbacks"
_TELEMETRY: Literal["telemetry"] = "telemetry"
_CONTEXT_CALL: Literal["context"] = "context"
_WAF_CALL: Literal["waf_run"] = "waf_run"
_BLOCK_CALL: Literal["block"] = "block"


GLOBAL_CALLBACKS: Dict[str, List[Callable]] = {_CONTEXT_CALL: []}


def report_error_on_span(error: str, message: str) -> None:
    span = getattr(_get_asm_context(), "span", None) or core.get_span()
    if not span:
        root_span = core.get_root_span()
    else:
        root_span = span._local_root or span
    if not root_span:
        return
    root_span.set_tag_str(APPSEC.ERROR_TYPE, error)
    root_span.set_tag_str(APPSEC.ERROR_MESSAGE, message)


class ASM_Environment:
    """
    an object of this class contains all asm data (waf and telemetry)
    for a single request. It is bound to a single asm request context.
    It is contained into a ContextVar.
    """

    def __init__(self, span: Optional[Span] = None):
        self.root = not in_asm_context()
        if self.root:
            core.add_suppress_exception(BlockingException)
        # add several layers of fallbacks to get a span, but normal span should be the first or the second one
        context_span = span or core.get_root_span()
        if context_span is None:
            logger.warning(WARNING_TAGS.ASM_ENV_NO_SPAN, extra=log_extra, stack_info=True)
            raise TypeError("ASM_Environment requires a span")
        self.span: Span = context_span
        if self.span.name.endswith(".request"):
            self.framework = self.span.name[:-8]
        else:
            self.framework = self.span.name
        self.framework = self.framework.lower().replace(" ", "_")
        self.waf_info: Optional[Callable[[], DDWaf_info]] = None
        self.waf_addresses: Dict[str, Any] = {}
        self.callbacks: Dict[str, Any] = {_CONTEXT_CALL: []}
        self.telemetry: Telemetry_result = Telemetry_result()
        self.addresses_sent: Set[str] = set()
        self.waf_triggers: List[Dict[str, Any]] = []
        self.blocked: Optional[Dict[str, Any]] = None
        self.finalized: bool = False
        self.api_security_reported: int = 0


def _get_asm_context() -> Optional[ASM_Environment]:
    return core.get_item(_ASM_CONTEXT)


def in_asm_context() -> bool:
    return core.get_item(_ASM_CONTEXT) is not None


def is_blocked() -> bool:
    env = _get_asm_context()
    if env is None:
        return False
    return env.blocked is not None


def get_blocked() -> Dict[str, Any]:
    env = _get_asm_context()
    if env is None:
        return {}
    return env.blocked or {}


def get_framework() -> str:
    env = _get_asm_context()
    if env is None:
        return ""
    return env.framework


def _use_html(headers) -> bool:
    """decide if the response should be html or json.

    Add support for quality values in the Accept header.
    """
    ctype = headers.get("Accept", headers.get("accept", ""))
    if not ctype:
        return False
    html_score = 0.0
    json_score = 0.0
    ctypes = ctype.split(",")
    for ct in ctypes:
        if len(ct) > 128:
            # ignore long (and probably malicious) headers to avoid performances issues
            continue
        m = re.match(r"([^/;]+/[^/;]+)(?:;q=([01](?:\.\d*)?))?", ct.strip())
        if m:
            if m.group(1) == "text/html":
                html_score = max(html_score, min(1.0, float(1.0 if m.group(2) is None else m.group(2))))
            elif m.group(1) == "text/*":
                html_score = max(html_score, min(1.0, float(0.2 if m.group(2) is None else m.group(2))))
            elif m.group(1) == "application/json":
                json_score = max(json_score, min(1.0, float(1.0 if m.group(2) is None else m.group(2))))
            elif m.group(1) == "application/*":
                json_score = max(json_score, min(1.0, float(0.2 if m.group(2) is None else m.group(2))))
    return html_score > json_score


def _ctype_from_headers(block_config, headers) -> None:
    """compute MIME type of the blocked response and store it in the block config"""
    desired_type = block_config.get("type", "auto")
    if desired_type == "auto":
        block_config["content-type"] = "text/html" if _use_html(headers) else "application/json"
    else:
        block_config["content-type"] = "text/html" if block_config["type"] == "html" else "application/json"


def set_blocked(blocked: Dict[str, Any]) -> None:
    blocked = blocked.copy()
    env = _get_asm_context()
    if env is None:
        logger.warning(WARNING_TAGS.SET_BLOCKED_NO_ASM_CONTEXT, extra=log_extra, stack_info=True)
        return
    _ctype_from_headers(blocked, get_headers())
    env.blocked = blocked


def update_span_metrics(span: Span, name: str, value: Union[float, int]) -> None:
    span.set_metric(name, value + (span.get_metric(name) or 0.0))


def flush_waf_triggers(env: ASM_Environment) -> None:
    from ddtrace.appsec._metrics import ddwaf_version

    # Make sure we find a root span to attach the triggers to
    root_span = env.span._local_root or env.span
    if env.waf_triggers:
        report_list = get_triggers(root_span)
        if report_list is not None:
            report_list.extend(env.waf_triggers)
        else:
            report_list = env.waf_triggers
        if asm_config._use_metastruct_for_triggers:
            root_span.set_struct_tag(APPSEC.STRUCT, {"triggers": report_list})
        else:
            root_span.set_tag(APPSEC.JSON, json.dumps({"triggers": report_list}, separators=(",", ":")))
        env.waf_triggers = []
    telemetry_results: Telemetry_result = env.telemetry

    root_span.set_tag_str(APPSEC.WAF_VERSION, ddwaf_version)
    if telemetry_results.total_duration:
        update_span_metrics(root_span, APPSEC.WAF_DURATION, telemetry_results.duration)
        telemetry_results.duration = 0.0
        update_span_metrics(root_span, APPSEC.WAF_DURATION_EXT, telemetry_results.total_duration)
        telemetry_results.total_duration = 0.0
    if telemetry_results.timeout:
        update_span_metrics(root_span, APPSEC.WAF_TIMEOUTS, telemetry_results.timeout)
    rasp_timeouts = sum(telemetry_results.rasp.timeout.values())
    if rasp_timeouts:
        update_span_metrics(root_span, APPSEC.RASP_TIMEOUTS, rasp_timeouts)
    if telemetry_results.rasp.sum_eval:
        update_span_metrics(root_span, APPSEC.RASP_DURATION, telemetry_results.rasp.duration)
        update_span_metrics(root_span, APPSEC.RASP_DURATION_EXT, telemetry_results.rasp.total_duration)
        update_span_metrics(root_span, APPSEC.RASP_RULE_EVAL, telemetry_results.rasp.sum_eval)
    if telemetry_results.truncation.string_length:
        root_span.set_metric(APPSEC.TRUNCATION_STRING_LENGTH, max(telemetry_results.truncation.string_length))
    if telemetry_results.truncation.container_size:
        root_span.set_metric(APPSEC.TRUNCATION_CONTAINER_SIZE, max(telemetry_results.truncation.container_size))
    if telemetry_results.truncation.container_depth:
        root_span.set_metric(APPSEC.TRUNCATION_CONTAINER_DEPTH, max(telemetry_results.truncation.container_depth))


def finalize_asm_env(env: ASM_Environment) -> None:
    if env.finalized:
        return
    env.finalized = True
    for function in GLOBAL_CALLBACKS[_CONTEXT_CALL]:
        function(env)
    flush_waf_triggers(env)
    for function in env.callbacks[_CONTEXT_CALL]:
        function(env)
    root_span = env.span._local_root or env.span
    if root_span:
        if env.waf_info:
            info = env.waf_info()
            try:
                if info.errors:
                    root_span.set_tag_str(APPSEC.EVENT_RULE_ERRORS, info.errors)
                    extra = {"product": "appsec", "more_info": info.errors, "stack_limit": 4}
                    logger.debug("asm_context::finalize_asm_env::waf_errors", extra=extra, stack_info=True)
                root_span.set_tag_str(APPSEC.EVENT_RULE_VERSION, info.version)
                root_span.set_metric(APPSEC.EVENT_RULE_LOADED, info.loaded)
                root_span.set_metric(APPSEC.EVENT_RULE_ERROR_COUNT, info.failed)
            except Exception:
                logger.debug("asm_context::finalize_asm_env::exception", extra=log_extra, exc_info=True)
        if asm_config._rc_client_id is not None:
            root_span._local_root.set_tag(APPSEC.RC_CLIENT_ID, asm_config._rc_client_id)
        waf_adresses = env.waf_addresses
        req_headers = waf_adresses.get(SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, {})
        if req_headers:
            _set_headers(root_span, req_headers, kind="request")
        res_headers = waf_adresses.get(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, {})
        if res_headers:
            _set_headers(root_span, res_headers, kind="response")

    core.discard_local_item(_ASM_CONTEXT)


def set_value(category: str, address: str, value: Any) -> None:
    env = _get_asm_context()
    if env is None:
        extra = {"product": "appsec", "more_info": f"::{category}::{address}", "stack_limit": 4}
        logger.debug("asm_context::set_value::no_active_context", extra=extra, stack_info=True)
        return
    asm_context_attr = getattr(env, category, None)
    if asm_context_attr is not None:
        asm_context_attr[address] = value


def set_headers_response(headers: Any) -> None:
    if headers is not None:
        set_waf_address(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, headers)


def set_body_response(body_response):
    # local import to avoid circular import
    from ddtrace.appsec._utils import parse_response_body

    set_waf_address(
        SPAN_DATA_NAMES.RESPONSE_BODY,
        lambda: parse_response_body(
            body_response,
            get_waf_address(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES),
        ),
    )


def set_waf_address(address: str, value: Any) -> None:
    if address == SPAN_DATA_NAMES.REQUEST_URI_RAW:
        parse_address = parse.urlparse(value)
        no_scheme = parse.ParseResult("", "", *parse_address[2:])
        waf_value = parse.urlunparse(no_scheme)
        set_value(_WAF_ADDRESSES, address, waf_value)
    else:
        set_value(_WAF_ADDRESSES, address, value)
    if address in (SPAN_DATA_NAMES.REQUEST_HTTP_IP, SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES_CASE):
        core.set_item(address, value)


def get_value(category: str, address: str, default: Any = None) -> Any:
    env = _get_asm_context()
    if env is None:
        extra = {"product": "appsec", "more_info": f"::{category}::{address}", "stack_limit": 4}
        logger.debug("asm_context::get_value::no_active_context", extra=extra, stack_info=True)
        return default
    asm_context_attr = getattr(env, category, None)
    if asm_context_attr is not None:
        return asm_context_attr.get(address, default)
    return default


def get_waf_address(address: str, default: Any = None) -> Any:
    return get_value(_WAF_ADDRESSES, address, default=default)


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


def set_waf_info(info: Callable[[], DDWaf_info]) -> None:
    env = _get_asm_context()
    if env is None:
        logger.warning(WARNING_TAGS.SET_WAF_INFO_NO_ASM_CONTEXT, extra=log_extra, stack_info=True)
        return
    env.waf_info = info


def call_waf_callback(custom_data: Optional[Dict[str, Any]] = None, **kwargs) -> Optional[DDWaf_result]:
    if not asm_config._asm_enabled:
        return None
    callback = get_value(_CALLBACKS, _WAF_CALL)
    if callback:
        return callback(custom_data, **kwargs)
    else:
        logger.warning(WARNING_TAGS.CALL_WAF_CALLBACK_NOT_SET, extra=log_extra, stack_info=True)
        report_error_on_span("appsec::instrumentation::diagnostic", WARNING_TAGS.CALL_WAF_CALLBACK_NOT_SET)
        return None


def call_waf_callback_no_instrumentation() -> None:
    """call the waf once if it was not already called"""
    if asm_config._asm_enabled:
        env = _get_asm_context()
        if env and not env.telemetry.triggered:
            callback = env.callbacks.get(_WAF_CALL)
            if callback:
                callback()


def set_ip(ip: Optional[str]) -> None:
    if ip is not None:
        set_waf_address(SPAN_DATA_NAMES.REQUEST_HTTP_IP, ip)


def get_ip() -> Optional[str]:
    return get_value(_WAF_ADDRESSES, SPAN_DATA_NAMES.REQUEST_HTTP_IP)


# Note: get/set headers use Any since we just carry the headers here without changing or using them
# and different frameworks use different types that we don't want to force it into a Mapping at the
# early point set_headers is usually called


def set_headers(headers: Any) -> None:
    if headers is not None:
        set_waf_address(SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, headers)


def get_headers() -> Optional[Any]:
    return get_value(_WAF_ADDRESSES, SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, {})


def set_headers_case_sensitive(case_sensitive: bool) -> None:
    set_waf_address(SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES_CASE, case_sensitive)


def get_headers_case_sensitive() -> bool:
    return get_value(_WAF_ADDRESSES, SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES_CASE, False)  # type : ignore


def set_block_request_callable(_callable: Optional[Callable], *_) -> None:
    """
    Sets a callable that could be use to do a best-effort to block the request. If
    the callable need any params, like headers, they should be curried with
    functools.partial.
    """
    if asm_config._asm_enabled and _callable:
        set_value(_CALLBACKS, _BLOCK_CALL, _callable)


def block_request() -> None:
    """
    Calls or returns the stored block request callable, if set.
    """
    _callable = get_value(_CALLBACKS, _BLOCK_CALL)
    if _callable:
        _callable()
    else:
        logger.warning(WARNING_TAGS.BLOCK_REQUEST_NOT_CALLABLE, extra=log_extra, stack_info=True)


def get_data_sent() -> Set[str]:
    env = _get_asm_context()
    if env is None:
        logger.warning(WARNING_TAGS.GET_DATA_SENT_NO_ASM_CONTEXT, extra=log_extra, stack_info=True)
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


def set_waf_telemetry_results(
    rules_version: str,
    is_blocked: bool,
    waf_results: DDWaf_result,
    rule_type: Optional[str],
    is_sampled: bool,
) -> None:
    env = _get_asm_context()
    if env is None:
        return
    result: Telemetry_result = env.telemetry
    is_triggered = bool(waf_results.data)
    from ddtrace.appsec._metrics import _report_waf_truncations

    result.rate_limited |= is_sampled
    if waf_results.return_code < 0:
        if result.error:
            result.error = max(result.error, waf_results.return_code)
        else:
            result.error = waf_results.return_code
        from ddtrace.appsec._metrics import _report_waf_run_error

        _report_waf_run_error(waf_results.return_code, rules_version, rule_type)
    _report_waf_truncations(waf_results.truncation)
    for key in ["container_size", "container_depth", "string_length"]:
        res = getattr(waf_results.truncation, key)
        if isinstance(res, int):
            getattr(result.truncation, key).append(res)
    if rule_type is None:
        # Request Blocking telemetry
        result.triggered |= is_triggered
        result.blocked |= is_blocked
        result.timeout += waf_results.timeout
        if rules_version:
            result.version = rules_version
        result.duration += waf_results.runtime
        result.total_duration += waf_results.total_runtime
    else:
        # Exploit Prevention telemetry
        result.rasp.blocked |= is_blocked
        result.rasp.sum_eval += 1
        result.rasp.eval[rule_type] += 1
        result.rasp.match[rule_type] += int(is_triggered)
        result.rasp.timeout[rule_type] += int(waf_results.timeout)
        result.rasp.durations[rule_type] += waf_results.runtime
        result.rasp.duration += waf_results.runtime
        result.rasp.total_duration += waf_results.total_runtime


def get_waf_telemetry_results() -> Optional[Telemetry_result]:
    env = _get_asm_context()
    if env:
        return env.telemetry
    return None


def store_waf_results_data(data) -> None:
    if not data:
        return
    env = _get_asm_context()
    if env is None:
        logger.warning(WARNING_TAGS.STORE_WAF_RESULTS_NO_ASM_CONTEXT, extra=log_extra, stack_info=True)
        return
    for d in data:
        d["span_id"] = env.span.span_id
    env.waf_triggers.extend(data)


def start_context(span: Span):
    if asm_config._asm_enabled:
        # it should only be called at start of a core context, when ASM_Env is not set yet
        core.set_item(_ASM_CONTEXT, ASM_Environment(span=span))
        asm_request_context_set(
            core.get_local_item("remote_addr"),
            core.get_local_item("headers"),
            core.get_local_item("headers_case_sensitive"),
            core.get_local_item("block_request_callable"),
        )


def end_context(span: Span):
    env = _get_asm_context()
    if env is not None and env.span is span:
        finalize_asm_env(env)


def _on_context_ended(ctx):
    env = ctx.get_local_item(_ASM_CONTEXT)
    if env is not None:
        finalize_asm_env(env)


def _on_wrapped_view(kwargs):
    callback_block = None
    # if Appsec is enabled, we can try to block as we have the path parameters at that point
    if asm_config._asm_enabled and in_asm_context():
        logger.debug("asm_context::flask::srb_on_request_param", extra=log_extra)
        if kwargs:
            set_waf_address(REQUEST_PATH_PARAMS, kwargs)
        call_waf_callback()
        if is_blocked():
            callback_block = get_value(_CALLBACKS, "flask_block")
    return callback_block


def _on_pre_tracedrequest(ctx):
    current_span = ctx.span
    block_request_callable = ctx.get_item("block_request_callable")
    if asm_config._asm_enabled:
        set_block_request_callable(functools.partial(block_request_callable, current_span))
        if get_blocked():
            block_request()


def _set_headers_and_response(response, headers, *_):
    if not asm_config._asm_enabled:
        return

    if asm_config._api_security_feature_active:
        if headers:
            # start_response was not called yet, set the HTTP response headers earlier
            if isinstance(headers, dict):
                list_headers = list(headers.items())
            else:
                list_headers = list(headers)
            set_headers_response(list_headers)
        if response and asm_config._api_security_parse_response_body:
            set_body_response(response)


def _call_waf_first(integration, *_) -> None:
    if not asm_config._asm_enabled:
        return
    info = f"{integration}::srb_on_request"
    logger.debug(info, extra=log_extra)
    call_waf_callback()


def _call_waf(integration, *_) -> None:
    if not asm_config._asm_enabled:
        return
    info = f"{integration}::srb_on_response"
    logger.debug(info, extra=log_extra)
    call_waf_callback()


def _on_block_decided(callback):
    if not asm_config._asm_enabled:
        return

    set_value(_CALLBACKS, "flask_block", callback)
    core.on("flask.block.request.content", callback, "block_requested")


def _get_headers_if_appsec():
    if asm_config._asm_enabled:
        return get_headers()


## headers tags

_COLLECTED_REQUEST_HEADERS_ASM_ENABLED = {
    "accept",
    "content-type",
    "user-agent",
    "x-amzn-trace-id",
    "cloudfront-viewer-ja3-fingerprint",
    "cf-ray",
    "x-cloud-trace-context",
    "x-appgw-trace-id",
    "akamai-user-risk",
    "x-sigsci-requestid",
    "x-sigsci-tags",
}

_COLLECTED_REQUEST_HEADERS = {
    "accept-encoding",
    "accept-language",
    "cf-connecting-ip",
    "cf-connecting-ipv6",
    "content-encoding",
    "content-language",
    "content-length",
    "fastly-client-ip",
    "forwarded",
    "forwarded-for",
    "host",
    "true-client-ip",
    "via",
    "x-client-ip",
    "x-cluster-client-ip",
    "x-forwarded",
    "x-forwarded-for",
    "x-real-ip",
}

_COLLECTED_REQUEST_HEADERS.update(_COLLECTED_REQUEST_HEADERS_ASM_ENABLED)


def _set_headers(span: Span, headers: Any, kind: str, only_asm_enabled: bool = False) -> None:
    for k in headers:
        if isinstance(k, tuple):
            key, value = k
        else:
            key, value = k, headers[k]
        if isinstance(key, bytes):
            key = key.decode()
        if isinstance(value, bytes):
            value = value.decode()
        if key.lower() in (_COLLECTED_REQUEST_HEADERS_ASM_ENABLED if only_asm_enabled else _COLLECTED_REQUEST_HEADERS):
            # since the header value can be a list, use `set_tag()` to ensure it is converted to a string
            (span._local_root or span).set_tag(_normalize_tag_name(kind, key), value)


def asm_listen():
    core.on("flask.finalize_request.post", _set_headers_and_response)
    core.on("flask.wrapped_view", _on_wrapped_view, "callbacks")
    core.on("flask._patched_request", _on_pre_tracedrequest)
    core.on("wsgi.block_decided", _on_block_decided)
    core.on("flask.start_response", _call_waf_first)

    core.on("django.start_response.post", _call_waf_first)
    core.on("django.finalize_response", _call_waf)
    core.on("django.after_request_headers", _get_headers_if_appsec, "headers")
    core.on("django.extract_body", _get_headers_if_appsec, "headers")
    core.on("django.after_request_headers.finalize", _set_headers_and_response)

    core.on("asgi.start_request", _call_waf_first)
    core.on("asgi.start_response", _call_waf)
    core.on("asgi.finalize_response", _set_headers_and_response)

    core.on("asm.set_blocked", set_blocked)
    core.on("asm.get_blocked", get_blocked, "block_config")

    core.on("context.ended.wsgi.__call__", _on_context_ended)
    core.on("context.ended.asgi.__call__", _on_context_ended)

    core.on("context.ended.django.traced_get_response", _on_context_ended)
    core.on("django.traced_get_response.pre", set_block_request_callable)
