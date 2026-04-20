import io
import json
from typing import Iterable
from typing import Union

from ddtrace.appsec._asm_request_context import _get_asm_context
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._constants import RASP_CAPABILITY
from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.appsec._contrib.stripe.patch import patch as patch_stripe
from ddtrace.appsec._contrib.stripe.patch import unpatch as unpatch_stripe
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._patch_utils import try_unwrap
from ddtrace.appsec._patch_utils import try_wrap_function_wrapper
import ddtrace.contrib.internal.subprocess.patch as subprocess_patch
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.settings.asm import config as asm_config


log = get_logger(__name__)

_is_patched = False


def patch_common_modules():
    global _is_patched

    @ModuleWatchdog.after_module_imported("subprocess")
    def _(module):
        # ensure that the subprocess patch is applied even after one click activation
        subprocess_patch.patch()
        log.debug("Patching common modules: subprocess_patch")

    if _is_patched:
        return

    try_wrap_function_wrapper(
        "urllib3.connectionpool", "HTTPConnectionPool._make_request", wrapped_urllib3_make_request_6D4E8B2A1F095C73
    )
    try_wrap_function_wrapper("urllib3.connectionpool", "HTTPConnectionPool.urlopen", wrapped_urllib3_urlopen)
    try_wrap_function_wrapper("urllib3._request_methods", "RequestMethods.request", wrapped_request_D8CB81E472AF98A2)
    try_wrap_function_wrapper("urllib3.request", "RequestMethods.request", wrapped_request_D8CB81E472AF98A2)
    try_wrap_function_wrapper("urllib.request", "OpenerDirector.open", wrapped_open_ED4CF71136E15EBF)
    try_wrap_function_wrapper("http.client", "HTTPConnection.request", wrapped_request_A7F2C6E4D3B10958)
    try_wrap_function_wrapper("http.client", "HTTPConnection.getresponse", wrapped_response)

    patch_stripe()

    log.debug("Patching common modules: builtins and urllib.request")
    _is_patched = True


def unpatch_common_modules():
    global _is_patched
    if not _is_patched:
        return

    try_unwrap("urllib3.connectionpool", "HTTPConnectionPool._make_request")
    try_unwrap("urllib3._request_methods", "RequestMethods.request")
    try_unwrap("urllib3.request", "RequestMethods.request")
    try_unwrap("urllib.request", "OpenerDirector.open")
    try_unwrap("http.client", "HTTPConnection.request")
    try_unwrap("http.client", "HTTPConnection.getresponse")
    try_unwrap("_io", "BytesIO.read")
    try_unwrap("_io", "StringIO.read")

    unpatch_stripe()

    subprocess_patch.unpatch()

    log.debug("Unpatching common modules subprocess, builtins and urllib.request")
    _is_patched = False


def _must_block(actions: Iterable[str]) -> bool:
    return any(action in (WAF_ACTIONS.BLOCK_ACTION, WAF_ACTIONS.REDIRECT_ACTION) for action in actions)


def _get_rasp_capability(capability: RASP_CAPABILITY) -> bool:
    """Check if the RASP capability is enabled."""
    if asm_config._asm_enabled and asm_config._ep_enabled:
        from ddtrace.appsec._asm_request_context import in_asm_context

        if not in_asm_context():
            return False

        try:
            from ddtrace.appsec._processor import AppSecSpanProcessor
        except Exception as e:
            from ddtrace.appsec._listeners import _abort_appsec

            _abort_appsec(str(e))
            return False

        return AppSecSpanProcessor.rasp_enabled(capability)
    return False


def _build_headers(lst: Iterable[tuple[str, str]]) -> dict[str, Union[str, list[str]]]:
    res: dict[str, Union[str, list[str]]] = {}
    for a, b in lst:
        if a in res:
            v = res[a]
            if isinstance(v, str):
                res[a] = [v, b]
            else:
                v.append(b)
        else:
            res[a] = b
    return res


def wrapped_request_A7F2C6E4D3B10958(original_request_callable, instance, args, kwargs):
    full_url = core.find_item("full_url")
    env = _get_asm_context()
    if _get_rasp_capability("ssrf") and full_url is not None and env is not None:
        use_body = core.find_item("use_body", False)
        method = args[0] if len(args) > 0 else kwargs.get("method", None)
        body = args[2] if len(args) > 2 else kwargs.get("body", None)
        headers = args[3] if len(args) > 3 else kwargs.get("headers", {})
        addresses = {EXPLOIT_PREVENTION.ADDRESS.SSRF: full_url, "DOWN_REQ_METHOD": method, "DOWN_REQ_HEADERS": headers}
        content_type = headers.get("Content-Type", None) or headers.get("content-type", None)
        if use_body and content_type == "application/json":
            try:
                addresses["DOWN_REQ_BODY"] = json.loads(body)
            except Exception:
                pass  # nosec
        res = call_waf_callback(
            addresses,
            crop_trace="wrapped_request_A7F2C6E4D3B10958",
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_REQ,
        )
        env.downstream_requests += 1
        core.discard_item("full_url")
        if res and _must_block(res.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, full_url)
    return original_request_callable(*args, **kwargs)


def wrapped_response(original_response_callable, instance, args, kwargs):
    response = original_response_callable(*args, *kwargs)
    env = _get_asm_context()
    try:
        if _get_rasp_capability("ssrf") and response.__class__.__name__ == "HTTPResponse" and env is not None:
            status = response.getcode()
            if 300 <= status < 400:
                # api10 for redirected response status and headers in urllib
                addresses = {
                    "DOWN_RES_STATUS": str(status),
                    "DOWN_RES_HEADERS": _build_headers(response.getheaders()),
                }
                call_waf_callback(addresses, rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES)
    except Exception:
        pass  # nosec
    return response


def _parse_http_response_body(response):
    try:
        if response.length and response.headers.get("content-type", None) == "application/json":
            length = response.length
            body = response.read()
            response.fp = io.BytesIO(body)
            response.length = length
            return json.loads(body)
    except Exception:
        return None
    return None


def wrapped_open_ED4CF71136E15EBF(original_open_callable, instance, args, kwargs):
    """
    wrapper for open url function
    """
    if _get_rasp_capability("ssrf"):
        try:
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import should_analyze_body_response
        except ImportError:
            # open is used during module initialization
            # and shouldn't be changed at that time
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, True)
            return original_open_callable(*args, **kwargs)

        url = args[0] if args else kwargs.get("fullurl", None)
        if url.__class__.__name__ == "Request":
            url = url.get_full_url()
        valid_url = isinstance(url, str) and bool(url)
        if valid_url and url and (ctx := _get_asm_context()):
            use_body = should_analyze_body_response(ctx)
            with core.context_with_data("url_open_analysis", full_url=url, use_body=use_body):
                # API10, doing all request calls in HTTPConnection.request
                try:
                    response = original_open_callable(*args, **kwargs)
                    # api10 response handler for regular responses
                    if response.__class__.__name__ == "HTTPResponse" and not (300 <= response.status < 400):
                        addresses = {
                            "DOWN_RES_STATUS": str(response.status),
                            "DOWN_RES_HEADERS": _build_headers(response.getheaders()),
                        }
                        if use_body:
                            addresses["DOWN_RES_BODY"] = _parse_http_response_body(response)
                        call_waf_callback(addresses, rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES)
                    return response
                except Exception as e:
                    # api10 response handler for error responses
                    if e.__class__.__name__ == "HTTPError":
                        try:
                            status_code = e.code
                        except Exception:
                            status_code = None
                        try:
                            response_headers = _build_headers(e.headers.items())
                        except Exception:
                            response_headers = None
                        if status_code is not None or response_headers is not None:
                            call_waf_callback(
                                {"DOWN_RES_STATUS": str(status_code), "DOWN_RES_HEADERS": response_headers},
                                rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
                            )
                    raise
        elif valid_url:
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
    return original_open_callable(*args, **kwargs)


def _parse_headers_urllib3(headers):
    try:
        return dict(headers)
    except Exception:
        return {}


def wrapped_urllib3_make_request_6D4E8B2A1F095C73(original_request_callable, instance, args, kwargs):
    full_url = core.find_item("full_url")
    env = _get_asm_context()
    do_rasp = _get_rasp_capability("ssrf") and full_url is not None and env is not None
    if do_rasp:
        use_body = core.find_item("use_body", False)
        method = args[1] if len(args) > 1 else kwargs.get("method", None)
        body = args[3] if len(args) > 3 else kwargs.get("body", None)
        headers = _parse_headers_urllib3(args[4] if len(args) > 4 else kwargs.get("headers", {}))
        addresses = {EXPLOIT_PREVENTION.ADDRESS.SSRF: full_url, "DOWN_REQ_METHOD": method, "DOWN_REQ_HEADERS": headers}
        content_type = headers.get("Content-Type", None) or headers.get("content-type", None)
        if use_body and content_type == "application/json":
            try:
                addresses["DOWN_REQ_BODY"] = json.loads(body)
            except Exception:
                pass  # nosec
        res = call_waf_callback(
            addresses,
            crop_trace="wrapped_urllib3_make_request_6D4E8B2A1F095C73",
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_REQ,
        )
        env.downstream_requests += 1
        core.discard_item("full_url")
        if res and _must_block(res.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, full_url)
    response = original_request_callable(*args, **kwargs)
    try:
        if do_rasp and response.__class__.__name__ == "BaseHTTPResponse" and 300 <= response.status < 400:
            # api10 for redirected response status and headers in urllib3
            addresses = {
                "DOWN_RES_STATUS": str(response.status),
                "DOWN_RES_HEADERS": response.headers,
            }
            call_waf_callback(addresses, rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES)
    except Exception:
        pass  # nosec
    return response


def wrapped_urllib3_urlopen(original_open_callable, instance, args, kwargs):
    full_url = args[2] if len(args) > 2 else kwargs.get("url", None)
    if core.find_item("full_url") is None:
        core.set_item("full_url", full_url)
    try:
        return original_open_callable(*args, **kwargs)
    finally:
        core.discard_item("full_url")


def wrapped_request_D8CB81E472AF98A2(original_request_callable, instance, args, kwargs):
    """
    wrapper for third party requests.request function
    https://requests.readthedocs.io
    """
    if _get_rasp_capability("ssrf"):
        try:
            from ddtrace.appsec._asm_request_context import _get_asm_context
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import should_analyze_body_response
        except ImportError:
            # open is used during module initialization
            # and shouldn't be changed at that time
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, True)
            return original_request_callable(*args, **kwargs)

        url = args[1] if len(args) > 1 else kwargs.get("url", None)
        valid_url = isinstance(url, str) and bool(url)
        if valid_url and url and (ctx := _get_asm_context()):
            use_body = should_analyze_body_response(ctx)
            with core.context_with_data("url_open_analysis", full_url=url, use_body=use_body):
                # API10, doing all request calls in HTTPConnection.request
                try:
                    response = original_request_callable(*args, **kwargs)
                    if response.__class__.__name__ == "Response" and not (300 <= response.status_code < 400):
                        addresses = {
                            "DOWN_RES_STATUS": str(response.status_code),
                            "DOWN_RES_HEADERS": dict(response.headers),
                        }
                        if use_body:
                            try:
                                addresses["DOWN_RES_BODY"] = response.json()
                            except Exception:
                                pass  # nosec
                        call_waf_callback(addresses, rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES)
                    return response
                except Exception:
                    raise
        elif valid_url:
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
    return original_request_callable(*args, **kwargs)
