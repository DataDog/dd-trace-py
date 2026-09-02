import io
import json
from typing import Iterable
from typing import Union
from urllib.parse import urlunparse

from ddtrace.appsec._asm_request_context import _get_asm_context
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import open_rasp_subcontext_scope
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._contrib.filesystem.patch import patch as patch_filesystem_for_appsec
from ddtrace.appsec._contrib.filesystem.patch import unpatch as unpatch_filesystem_for_appsec
from ddtrace.appsec._contrib.stripe.patch import patch as patch_stripe_for_appsec
from ddtrace.appsec._contrib.stripe.patch import unpatch as unpatch_stripe_for_appsec
from ddtrace.appsec._contrib.subprocess.patch import patch as patch_subprocess_for_appsec
from ddtrace.appsec._contrib.subprocess.patch import unpatch as unpatch_subprocess_for_appsec
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._patch_utils import try_unwrap
from ddtrace.appsec._patch_utils import try_unwrap_context
from ddtrace.appsec._patch_utils import try_wrap_context
from ddtrace.appsec._patch_utils import try_wrap_function_wrapper
from ddtrace.appsec._rasp import _must_block
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.logger import get_logger
from ddtrace.internal.wrapping.context import WrappingContext


log = get_logger(__name__)

_is_patched = False


def patch_common_modules() -> None:
    global _is_patched
    if _is_patched:
        return

    try_wrap_function_wrapper(
        "urllib3.connectionpool", "HTTPConnectionPool._make_request", wrapped_urllib3_make_request_6D4E8B2A1F095C73
    )
    try_wrap_function_wrapper("urllib3.connectionpool", "HTTPConnectionPool.urlopen", wrapped_urllib3_urlopen)
    try_wrap_function_wrapper("urllib3._request_methods", "RequestMethods.request", wrapped_request_D8CB81E472AF98A2)
    try_wrap_function_wrapper("urllib3.request", "RequestMethods.request", wrapped_request_D8CB81E472AF98A2)
    try_wrap_context("urllib.request", "OpenerDirector.open", _SsrfOpenerDirectorOpen)
    try_wrap_context("http.client", "HTTPConnection.request", _SsrfHttpConnectionRequest)
    try_wrap_context("http.client", "HTTPConnection.getresponse", _SsrfHttpConnectionGetresponse)

    patch_filesystem_for_appsec()
    patch_stripe_for_appsec()
    patch_subprocess_for_appsec()

    log.debug("Patching common modules: builtins and urllib.request")
    _is_patched = True


def unpatch_common_modules():
    global _is_patched
    if not _is_patched:
        return

    try_unwrap("urllib3.connectionpool", "HTTPConnectionPool._make_request")
    try_unwrap("urllib3.connectionpool", "HTTPConnectionPool.urlopen")
    try_unwrap("urllib3._request_methods", "RequestMethods.request")
    try_unwrap("urllib3.request", "RequestMethods.request")
    try_unwrap_context("urllib.request", "OpenerDirector.open")
    try_unwrap_context("http.client", "HTTPConnection.request")
    try_unwrap_context("http.client", "HTTPConnection.getresponse")
    unpatch_filesystem_for_appsec()
    unpatch_stripe_for_appsec()
    unpatch_subprocess_for_appsec()

    log.debug("Unpatching common modules subprocess, builtins and urllib.request")
    _is_patched = False


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


class _RaspContext(WrappingContext):
    """Base for RASP wrapping contexts: argument access by name, plus a core context held
    open across the wrapped call.
    """

    def __enter__(self) -> "_RaspContext":
        super().__enter__()
        self.set("core_ctx", None)
        return self

    def _arg(self, name: str, default=None):
        """Read a parameter of the wrapped call by name.

        Unlike get_local this tolerates an unbound name: a KeyError raised here would be
        swallowed by the universal context and silently disable the hook.
        """
        return self.__frame__.f_locals.get(name, default)

    def _rasp_active(self) -> bool:
        """True between _open_core_context and _close_core_context, i.e. RASP inspected this call."""
        return self.get("core_ctx") is not None

    def _open_core_context(self, name: str, **kwargs) -> None:
        core_ctx = core.context_with_data(name, **kwargs)
        core_ctx.__enter__()
        self.set("core_ctx", core_ctx)

    def _close_core_context(self) -> None:
        core_ctx = self.get("core_ctx")
        if core_ctx is not None:
            self.set("core_ctx", None)
            core_ctx.__exit__(None, None, None)


class _SsrfOpenerDirectorOpen(_RaspContext):
    """RASP SSRF analysis around urllib.request.OpenerDirector.open."""

    def __enter__(self) -> "_SsrfOpenerDirectorOpen":
        super().__enter__()
        if not get_rasp_capability("ssrf"):
            return self
        try:
            from ddtrace.appsec._asm_request_context import should_analyze_body_response
        except ImportError:
            # open is used during module initialization
            # and shouldn't be changed at that time
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, True)
            return self

        url = self._arg("fullurl")
        if url.__class__.__name__ == "Request":
            url = url.get_full_url()
        if not (isinstance(url, str) and url):
            return self

        ctx = _get_asm_context()
        if ctx is None:
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
            return self

        use_body = should_analyze_body_response(ctx)
        self.set("use_body", use_body)
        # This outgoing request's SSRF_REQ + SSRF_RES WAF calls share one subcontext.
        self._open_core_context("url_open_analysis", full_url=url, use_body=use_body)
        open_rasp_subcontext_scope()
        return self

    def __return__(self, response):
        if self._rasp_active():
            try:
                # api10 response handler for regular responses
                if response.__class__.__name__ == "HTTPResponse" and not (300 <= response.status < 400):
                    addresses = {
                        "DOWN_RES_STATUS": str(response.status),
                        "DOWN_RES_HEADERS": _build_headers(response.getheaders()),
                    }
                    if self.get("use_body"):
                        addresses["DOWN_RES_BODY"] = _parse_http_response_body(response)
                    call_waf_callback(addresses, rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES)
            finally:
                # Must run before a block raises: a raising __return__ suppresses __exit__.
                self._close_core_context()
        return super().__return__(response)

    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        if self._rasp_active():
            try:
                # api10 response handler for error responses
                if exc_value is not None and exc_value.__class__.__name__ == "HTTPError":
                    try:
                        status_code = exc_value.code
                    except Exception:
                        status_code = None
                    try:
                        response_headers = _build_headers(exc_value.headers.items())
                    except Exception:
                        response_headers = None
                    if status_code is not None or response_headers is not None:
                        call_waf_callback(
                            {"DOWN_RES_STATUS": str(status_code), "DOWN_RES_HEADERS": response_headers},
                            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
                        )
            finally:
                self._close_core_context()
        super().__exit__(exc_type, exc_value, exc_tb)


class _SsrfHttpConnectionRequest(_RaspContext):
    """RASP SSRF + API10 downstream-request analysis around http.client.HTTPConnection.request."""

    def __enter__(self) -> "_SsrfHttpConnectionRequest":
        super().__enter__()
        full_url = core.find_item("full_url")
        env = _get_asm_context()
        if get_rasp_capability("ssrf") and full_url is not None and env is not None:
            use_body = core.find_item("use_body", False)
            method = self._arg("method")
            body = self._arg("body")
            headers = self._arg("headers", {})
            addresses = {
                EXPLOIT_PREVENTION.ADDRESS.SSRF: full_url,
                "DOWN_REQ_METHOD": method,
                "DOWN_REQ_HEADERS": headers,
            }
            content_type = headers.get("Content-Type", None) or headers.get("content-type", None)
            if use_body and content_type == "application/json":
                try:
                    addresses["DOWN_REQ_BODY"] = json.loads(body)
                except Exception:
                    pass  # nosec
            res = call_waf_callback(
                addresses,
                # A wrapping context runs inside the target's own frame, so the crop anchor is the
                # wrapped function rather than a wrapper.
                crop_trace=self.__wrapped__.__name__,
                rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_REQ,
            )
            env.downstream_requests += 1
            core.discard_item("full_url")
            if res and _must_block(res.actions):
                raise BlockingException(
                    get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, full_url
                )
        return self


class _SsrfHttpConnectionGetresponse(WrappingContext):
    """API10 analysis of redirect responses around http.client.HTTPConnection.getresponse.

    Inspects only the return value, so it needs neither argument access nor a core context.
    """

    def __return__(self, response):
        env = _get_asm_context()
        try:
            if get_rasp_capability("ssrf") and response.__class__.__name__ == "HTTPResponse" and env is not None:
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
        return super().__return__(response)


def _parse_headers_urllib3(headers):
    try:
        return dict(headers)
    except Exception:
        return {}


def wrapped_urllib3_make_request_6D4E8B2A1F095C73(original_request_callable, instance, args, kwargs):
    full_url = core.find_item("full_url")
    env = _get_asm_context()
    do_rasp = get_rasp_capability("ssrf") and full_url is not None and env is not None
    if not do_rasp:
        return original_request_callable(*args, **kwargs)
    core.discard_item("full_url")
    # Run this outgoing request in its own core context so concurrent urllib3 requests each get a
    # distinct subcontext (shared only by this request's SSRF_REQ + SSRF_RES). When an outer client
    # (e.g. requests) already owns a scope, open_rasp_subcontext_scope finds it (walks up) and
    # reuses it. Dropping this context releases the holder, so no explicit close is needed.
    with core.context_with_data("rasp.ssrf.urllib3"):
        open_rasp_subcontext_scope()
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
        if res and _must_block(res.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, full_url)
        # api10 redirect (3xx) response analysis is intentionally NOT done here: urllib3 bottoms
        # out in http.client.HTTPConnection.getresponse (_SsrfHttpConnectionGetresponse), which
        # already sends DOWN_RES_STATUS/DOWN_RES_HEADERS for 3xx responses within this same SSRF
        # subcontext. Re-inspecting here would double-call the WAF.
        return original_request_callable(*args, **kwargs)


def _urllib3_absolute_url(instance, path: str) -> str:
    try:
        port = getattr(instance, "port", None)
        netloc = "{}:{}".format(instance.host, port) if port and port not in (80, 443) else str(instance.host)
        return urlunparse((instance.scheme, netloc, path, "", "", ""))
    except Exception:  # nosec
        return path


def wrapped_urllib3_urlopen(original_open_callable, instance, args, kwargs):
    # urlopen(method, url, ...): url is positional arg 1 (also on redirect re-invocation).
    full_url = args[1] if len(args) > 1 else kwargs.get("url", None)
    if isinstance(full_url, str) and full_url.startswith("/") and instance is not None:
        # PoolManager passes a relative URI; rebuild the absolute URL so SSRF/API10 sees the host.
        full_url = _urllib3_absolute_url(instance, full_url)
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
    if get_rasp_capability("ssrf"):
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
                # This outgoing request's SSRF_REQ + SSRF_RES WAF calls share one subcontext.
                open_rasp_subcontext_scope()
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
