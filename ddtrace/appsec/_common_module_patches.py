import io
import json
from types import TracebackType
from typing import Any
from typing import Iterable
from typing import Optional
from typing import Union
from urllib.parse import urlsplit
from urllib.parse import urlunparse

from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_active_asm_context
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import open_rasp_subcontext_scope
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._contrib.filesystem.patch import patch as patch_filesystem_for_appsec
from ddtrace.appsec._contrib.filesystem.patch import unpatch as unpatch_filesystem_for_appsec
from ddtrace.appsec._contrib.stripe.patch import patch as patch_stripe_for_appsec
from ddtrace.appsec._contrib.stripe.patch import unpatch as unpatch_stripe_for_appsec
from ddtrace.appsec._contrib.subprocess.patch import patch as patch_subprocess_for_appsec
from ddtrace.appsec._contrib.subprocess.patch import unpatch as unpatch_subprocess_for_appsec
from ddtrace.appsec._contrib.webbrowser.patch import patch as patch_webbrowser_for_appsec
from ddtrace.appsec._contrib.webbrowser.patch import unpatch as unpatch_webbrowser_for_appsec
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._rasp import _must_block
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.logger import get_logger
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.internal.wrapping.hooks import try_unwrap_context
from ddtrace.internal.wrapping.hooks import try_wrap_context


log = get_logger(__name__)

_is_patched = False


def patch_common_modules() -> None:
    global _is_patched
    if _is_patched:
        return

    try_wrap_context("urllib3.connectionpool", "HTTPConnectionPool.urlopen", _SsrfUrllib3Urlopen)
    try_wrap_context("urllib3.connectionpool", "HTTPConnectionPool._make_request", _SsrfUrllib3MakeRequest)
    # Exactly one of these exists per major version: v2 moved RequestMethods to _request_methods.
    try_wrap_context("urllib3._request_methods", "RequestMethods.request", _SsrfUrllib3Request)
    try_wrap_context("urllib3.request", "RequestMethods.request", _SsrfUrllib3Request)
    try_wrap_context("urllib.request", "urlopen", _SsrfUrllibUrlopen)
    try_wrap_context("urllib.request", "OpenerDirector.open", _SsrfOpenerDirectorOpen)
    try_wrap_context("http.client", "HTTPConnection.request", _SsrfHttpConnectionRequest)
    try_wrap_context("http.client", "HTTPConnection.getresponse", _SsrfHttpConnectionGetresponse)

    patch_filesystem_for_appsec()
    patch_stripe_for_appsec()
    patch_subprocess_for_appsec()
    patch_webbrowser_for_appsec()

    log.debug("Patching common modules: builtins and urllib.request")
    _is_patched = True


def unpatch_common_modules():
    global _is_patched
    if not _is_patched:
        return

    try_unwrap_context("urllib3.connectionpool", "HTTPConnectionPool._make_request")
    try_unwrap_context("urllib3.connectionpool", "HTTPConnectionPool.urlopen")
    try_unwrap_context("urllib3._request_methods", "RequestMethods.request")
    try_unwrap_context("urllib3.request", "RequestMethods.request")
    try_unwrap_context("urllib.request", "urlopen")
    try_unwrap_context("urllib.request", "OpenerDirector.open")
    try_unwrap_context("http.client", "HTTPConnection.request")
    try_unwrap_context("http.client", "HTTPConnection.getresponse")
    unpatch_filesystem_for_appsec()
    unpatch_stripe_for_appsec()
    unpatch_subprocess_for_appsec()
    unpatch_webbrowser_for_appsec()

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
    """Base for RASP wrapping contexts: reads the wrapped call's arguments by name."""

    # urllib3 v1 keeps body and headers inside these bags instead of as named parameters.
    _VARKWARGS = ("httplib_request_kw", "urlopen_kw")

    def _locals(self) -> dict[str, Any]:
        """The wrapped call's locals, to read its arguments by name.

        Read them with .get rather than get_local: an unbound name raises KeyError, which the
        universal context swallows, silently disabling the hook.
        """
        return self.__frame__.f_locals

    def _arg(self, name: str, default: Any = None) -> Any:
        """Read one parameter by name, falling back to the target's **kwargs bag.

        urllib3 v1 declares _make_request as (conn, method, url, timeout, chunked,
        **httplib_request_kw), so body and headers are not locals at all there.
        """
        frame_locals = self._locals()
        if name in frame_locals:
            return frame_locals[name]
        for bag in self._VARKWARGS:
            values = frame_locals.get(bag)
            if isinstance(values, dict) and name in values:
                return values[name]
        return default


class _ScopedRaspContext(_RaspContext):
    """A RASP context that also holds a core context open across the wrapped call.

    __enter__ and __return__/__exit__ are separate calls, so a with statement cannot span them.
    """

    def __enter__(self) -> "_ScopedRaspContext":
        super().__enter__()
        self.set("core_ctx", None)
        return self

    def _core_context(self) -> Any:
        """The core context this call holds open, if any.

        Read the storage directly: BaseWrappingContext.get is strict, and __exit__ reaching this
        after __return__ already popped would raise TypeError past the callers' try blocks.
        """
        storage = self._storage.get()
        return None if storage is None else storage.get("core_ctx")

    def _rasp_active(self) -> bool:
        """True between _open_core_context and _close_core_context, i.e. RASP inspected this call."""
        return self._core_context() is not None

    def _open_core_context(self, name: str, **kwargs: Any) -> None:
        core_ctx = core.context_with_data(name, **kwargs)
        core_ctx.__enter__()
        self.set("core_ctx", core_ctx)

    def _close_core_context(self) -> None:
        core_ctx = self._core_context()
        if core_ctx is not None:
            self.set("core_ctx", None)
            core_ctx.__exit__(None, None, None)


class _SsrfOpenerDirectorOpen(_ScopedRaspContext):
    """RASP SSRF analysis around urllib.request.OpenerDirector.open."""

    # The wrapped call's parameter holding the URL; urlopen names it differently.
    _URL_ARGUMENT = "fullurl"

    def __enter__(self) -> "_SsrfOpenerDirectorOpen":
        super().__enter__()
        try:
            self._handle_enter()
        except Exception:
            # AIDEV-NOTE: a context whose __enter__ raises is left out of the universal context's
            # entered list, so neither __return__ nor __exit__ runs and the core context strands.
            self._close_core_context()
            log.debug("Error handling SSRF instrumentation enter", exc_info=True)
        return self

    def _handle_enter(self) -> None:
        if not get_rasp_capability("ssrf"):
            return
        try:
            from ddtrace.appsec._asm_request_context import should_analyze_body_response
        except ImportError:
            # open is used during module initialization
            # and shouldn't be changed at that time
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, True)
            return

        url: Any = self._locals().get(self._URL_ARGUMENT)
        if url.__class__.__name__ == "Request":
            url = url.get_full_url()
        if not (isinstance(url, str) and url):
            return

        if core.find_item("full_url") == url:
            # An enclosing scope already owns this exact request - urlopen above us, or a
            # requests/urllib3 wrapper - so inspecting again would issue a second SSRF_REQ call.
            # Compare the URL, not just presence: an opener may rewrite it before delegating, and
            # that destination is the one that has to be evaluated.
            return

        ctx = get_active_asm_context()
        if ctx is None:
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
            return

        use_body = should_analyze_body_response(ctx)
        self.set("use_body", use_body)
        # This outgoing request's SSRF_REQ + SSRF_RES WAF calls share one subcontext.
        self._open_core_context("url_open_analysis", full_url=url, use_body=use_body)
        open_rasp_subcontext_scope()

    def __return__(self, response: Any) -> Any:
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
            except Exception:
                # Never fail the customer's call, and never let a raising __return__ reach the
                # universal context, which suppresses __exit__ and strands this call's storage.
                log.debug("Error handling SSRF instrumentation return", exc_info=True)
            finally:
                self._close_core_context()
        return super().__return__(response)

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        if self._rasp_active():
            try:
                # api10 response handler for error responses
                if exc_value is not None and exc_value.__class__.__name__ == "HTTPError":
                    http_error: Any = exc_value
                    try:
                        status_code = http_error.code
                    except Exception:
                        status_code = None
                    try:
                        response_headers = _build_headers(http_error.headers.items())
                    except Exception:
                        response_headers = None
                    if status_code is not None or response_headers is not None:
                        call_waf_callback(
                            {"DOWN_RES_STATUS": str(status_code), "DOWN_RES_HEADERS": response_headers},
                            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
                        )
            except Exception:
                log.debug("Error handling SSRF instrumentation exit", exc_info=True)
            finally:
                self._close_core_context()
        super().__exit__(exc_type, exc_value, exc_tb)


def _carries_a_host(url: str) -> bool:
    """Whether the URL has an authority, structurally.

    Not a substring test: a "://" inside a query string is the shape of the very payload SSRF
    rules exist to catch, so treating it as absolute would skip the host on the worst requests.
    """
    try:
        return bool(urlsplit(url).netloc)
    except Exception:
        return False


def _absolute_downstream_url(connection: Any, path: str) -> str:
    """Rebuild an absolute URL from the connection when only a request path is available.

    SSRF is a decision about the host, so a bare path is not something the WAF can evaluate.
    """
    try:
        # A CONNECT tunnel puts the proxy in host/port; the request is really for the tunnel
        # target, so SSRF has to be judged against that instead.
        host = getattr(connection, "_tunnel_host", None)
        port = getattr(connection, "_tunnel_port", None) if host else None
        if not host:
            host, port = connection.host, connection.port
        scheme = "https" if connection.default_port == 443 else "http"
        if ":" in host:
            # http.client stores an IPv6 literal unbracketed, but a URL needs the brackets back or
            # the authority does not parse and the WAF skips the address entirely.
            host = f"[{host}]"
        netloc = host if port in (None, connection.default_port) else f"{host}:{port}"
        return f"{scheme}://{netloc}{path}"
    except Exception:
        return path


class _SsrfUrllibUrlopen(_SsrfOpenerDirectorOpen):
    """The same analysis on urllib.request.urlopen, whose parameter is named url.

    install_opener accepts any object with an open method, so a custom opener bypasses
    OpenerDirector.open entirely and would otherwise go uninspected.
    """

    _URL_ARGUMENT = "url"


class _SsrfHttpConnectionRequest(_RaspContext):
    """RASP SSRF + API10 downstream-request analysis around http.client.HTTPConnection.request."""

    def __enter__(self) -> "_SsrfHttpConnectionRequest":
        super().__enter__()
        # Cheapest and most selective gate first: it is two config reads, whereas the two lookups
        # below cost a core context walk each on every instrumented downstream request.
        if not get_rasp_capability("ssrf"):
            return self
        full_url = core.find_item("full_url")
        env = get_active_asm_context()
        if full_url is not None and env is not None:
            use_body = core.find_item("use_body", False)
            frame_locals = self._locals()
            method = frame_locals.get("method")
            body: Any = frame_locals.get("body")
            headers = frame_locals.get("headers", {})
            if not _carries_a_host(full_url):
                # An enclosing republish can shadow the outer client's absolute URL with just the
                # request path, and SSRF cannot be judged without a host. See APPSEC-70046.
                full_url = _absolute_downstream_url(frame_locals.get("self"), frame_locals.get("url") or full_url)
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
                # No wrapper frame to anchor on, so use the target's own. co_name, not __name__:
                # report_stack matches f_code.co_name, and wraps copies __name__ onto decorators.
                crop_trace=self.__wrapped__.__code__.co_name,
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

    def __return__(self, response: Any) -> Any:
        # See the note in _SsrfHttpConnectionRequest.__enter__ on the check order.
        if not get_rasp_capability("ssrf"):
            return super().__return__(response)
        env = get_active_asm_context()
        try:
            if response.__class__.__name__ == "HTTPResponse" and env is not None:
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


def _urllib3_absolute_url(instance, path: str) -> str:
    try:
        port = getattr(instance, "port", None)
        netloc = "{}:{}".format(instance.host, port) if port and port not in (80, 443) else str(instance.host)
        return urlunparse((instance.scheme, netloc, path, "", "", ""))
    except Exception:  # nosec
        return path


class _SsrfUrllib3Urlopen(_ScopedRaspContext):
    """Publishes the absolute URL of a urllib3 request for the _make_request hook to inspect."""

    def __enter__(self) -> "_SsrfUrllib3Urlopen":
        super().__enter__()
        try:
            self._handle_enter()
        except Exception:
            self._close_core_context()
            log.debug("Error handling SSRF instrumentation enter", exc_info=True)
        return self

    def _handle_enter(self) -> None:
        if not get_rasp_capability("ssrf"):
            return
        if core.find_item("full_url") is not None:
            # An outer client already owns this outgoing request and published its URL.
            return

        full_url: Any = self._arg("url")
        if isinstance(full_url, str) and full_url.startswith("/"):
            # PoolManager passes a relative URI, so rebuild it or SSRF sees no host.
            full_url = _urllib3_absolute_url(self._arg("self"), full_url)
        if not (isinstance(full_url, str) and full_url):
            return

        # Scoped rather than set on the current context: an item set with no context of our own is
        # visible to ddtrace's own worker threads, and this releases it on both exit paths.
        self._open_core_context("rasp.ssrf.urllib3.urlopen", full_url=full_url)

    def __return__(self, response: Any) -> Any:
        self._close_core_context()
        return super().__return__(response)

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self._close_core_context()
        super().__exit__(exc_type, exc_value, exc_tb)


class _SsrfUrllib3MakeRequest(_ScopedRaspContext):
    """RASP SSRF + API10 downstream-request analysis around urllib3's _make_request."""

    def __enter__(self) -> "_SsrfUrllib3MakeRequest":
        super().__enter__()
        try:
            self._handle_enter()
        except Exception:
            self._close_core_context()
            log.debug("Error handling SSRF instrumentation enter", exc_info=True)
        return self

    def _handle_enter(self) -> None:
        if not get_rasp_capability("ssrf"):
            return
        full_url = core.find_item("full_url")
        env = get_active_asm_context()
        if full_url is None or env is None:
            return
        core.discard_item("full_url")

        # Own core context so concurrent urllib3 requests get distinct subcontexts. It has to span
        # the call: the SSRF_RES side comes from http.client's getresponse underneath this one.
        self._open_core_context("rasp.ssrf.urllib3")
        open_rasp_subcontext_scope()

        headers = _parse_headers_urllib3(self._arg("headers", {}))
        addresses = {
            EXPLOIT_PREVENTION.ADDRESS.SSRF: full_url,
            "DOWN_REQ_METHOD": self._arg("method"),
            "DOWN_REQ_HEADERS": headers,
        }
        content_type = headers.get("Content-Type", None) or headers.get("content-type", None)
        if core.find_item("use_body", False) and content_type == "application/json":
            try:
                addresses["DOWN_REQ_BODY"] = json.loads(self._arg("body"))
            except Exception:
                pass  # nosec
        res = call_waf_callback(
            addresses,
            crop_trace=self.__wrapped__.__code__.co_name,
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_REQ,
        )
        env.downstream_requests += 1
        if res and _must_block(res.actions):
            # Released before the block propagates, the way the other RASP contexts do it.
            self._close_core_context()
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, full_url)

    def __return__(self, response: Any) -> Any:
        # No API10 response analysis here: urllib3 bottoms out in http.client's getresponse, which
        # already reports DOWN_RES_* for this same subcontext. Re-inspecting would double-call.
        self._close_core_context()
        return super().__return__(response)

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self._close_core_context()
        super().__exit__(exc_type, exc_value, exc_tb)


class _SsrfUrllib3Request(_ScopedRaspContext):
    """Opens the subcontext scope for a urllib3 RequestMethods.request call.

    The response side is left to the hooks underneath: this method returns urllib3's own
    HTTPResponse, which the API10 response analysis does not inspect.
    """

    def __enter__(self) -> "_SsrfUrllib3Request":
        super().__enter__()
        try:
            self._handle_enter()
        except Exception:
            self._close_core_context()
            log.debug("Error handling SSRF instrumentation enter", exc_info=True)
        return self

    def _handle_enter(self) -> None:
        if not get_rasp_capability("ssrf"):
            return
        try:
            from ddtrace.appsec._asm_request_context import should_analyze_body_response
        except ImportError:
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, True)
            return

        url: Any = self._arg("url")
        if not (isinstance(url, str) and url):
            return
        ctx = get_active_asm_context()
        if ctx is None:
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
            return

        # This outgoing request's SSRF_REQ + SSRF_RES WAF calls share one subcontext.
        self._open_core_context("url_open_analysis", full_url=url, use_body=should_analyze_body_response(ctx))
        open_rasp_subcontext_scope()

    def __return__(self, response: Any) -> Any:
        self._close_core_context()
        return super().__return__(response)

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self._close_core_context()
        super().__exit__(exc_type, exc_value, exc_tb)


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
