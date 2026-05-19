from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Optional

from ddtrace.internal.schema.span_attribute_schema import SpanDirection


if TYPE_CHECKING:  # pragma: no cover
    from ddtrace.internal.settings._config import Config  # noqa:F401
    from ddtrace.trace import Span  # noqa:F401
    from ddtrace.trace import Tracer  # noqa:F401

from urllib.parse import quote
from urllib.parse import unquote_to_bytes

import wrapt

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal._exceptions import find_exception
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.utils import get_blocked
from ddtrace.internal.utils import set_blocked
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.propagation._utils import from_wsgi_header
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.vendor.debtcollector import deprecate


log = get_logger(__name__)

propagator = HTTPPropagator

config._add(
    "wsgi",
    dict(
        _default_service="wsgi",
        distributed_tracing=True,
    ),
)


def get_version() -> str:
    return ""


def _supported_versions() -> dict[str, str]:
    return {"wsgi": "*"}


class _DDWSGIMiddlewareBase(object):
    """Base WSGI middleware class.

    :param application: The WSGI application to apply the middleware to.
    :param tracer: [Deprecated] Global tracer will be used instead.
    :param int_config: Integration specific configuration object.
    :param app_is_iterator: Boolean indicating whether the wrapped app is a Python iterator
    """

    def __init__(
        self,
        application: Iterable,
        tracer: Optional["Tracer"],
        int_config: "Config",
        app_is_iterator: bool = False,
    ) -> None:
        if tracer is not None:
            deprecate(
                "The tracer parameter is deprecated",
                message="The global tracer will be used instead.",
                category=DDTraceDeprecationWarning,
                removal_version="5.0.0",
            )
        self.app = application
        self._config = int_config
        self.app_is_iterator = app_is_iterator

    @property
    def _request_span_name(self) -> str:
        "Returns the name of a request span. Example: `flask.request`"
        raise NotImplementedError

    @property
    def _application_span_name(self) -> str:
        "Returns the name of an application span. Example: `flask.application`"
        raise NotImplementedError

    @property
    def _response_span_name(self) -> str:
        "Returns the name of a response span. Example: `flask.response`"
        raise NotImplementedError

    def __call__(self, environ: Iterable, start_response: Callable) -> wrapt.ObjectProxy:
        headers = get_request_headers(environ)
        closing_iterable = ()
        not_blocked = True
        with core.context_with_data(
            "wsgi.__call__",
            remote_addr=environ.get("REMOTE_ADDR"),
            headers=headers,
            headers_case_sensitive=True,
            service=trace_utils.int_service(None, self._config),
            span_type=SpanTypes.WEB,
            span_name=(self._request_call_name if hasattr(self, "_request_call_name") else self._request_span_name),
            middleware_config=self._config,
            integration_config=self._config,
            distributed_headers=environ,
            environ=environ,
            middleware=self,
            span_key="req_span",
            activate_distributed_headers=True,
        ) as ctx:
            ctx.set_item("wsgi.construct_url", construct_url)

            def blocked_view():
                result = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
                    "wsgi.block.started", (ctx, construct_url)
                ).status_headers_content
                if result:
                    status, headers, content = result.value
                else:
                    status, headers, content = 403, [], ""
                return content, status, headers

            if get_blocked():
                content, status, headers = blocked_view()
                start_response(str(status), headers)
                closing_iterable = [content]
                not_blocked = False

            core.dispatch("wsgi.block_decided", (blocked_view,))
            stop_iteration_exception = None

            if not_blocked:
                core.dispatch("wsgi.request.prepare", (ctx, start_response))
                try:
                    closing_iterable = self.app(environ, ctx.find_item("intercept_start_response"))
                except BlockingException as e:
                    set_blocked(e.args[0])
                    content, status, headers = blocked_view()
                    start_response(str(status), headers)
                    closing_iterable = [content]
                    core.dispatch("wsgi.app.exception", (ctx,))
                except StopIteration as e:
                    """
                    WSGI frameworks emit `StopIteration` when closing connections
                    with Gunicorn as part of their standard workflow.  We don't want to mark
                    these as errors for the UI since they are standard and expected
                    to be caught later.

                    Here we catch the exception and close out the request/app spans through the non-error
                    handling pathways.
                    """
                    stop_iteration_exception = e
                except BaseException as exc:
                    # managing python 3.11+ BaseExceptionGroup with compatible code for 3.10 and below
                    if blocking_exc := find_exception(exc, BlockingException):
                        set_blocked(blocking_exc.args[0])
                        content, status, headers = blocked_view()
                        start_response(str(status), headers)
                        closing_iterable = [content]
                        core.dispatch("wsgi.app.exception", (ctx,))
                    else:
                        core.dispatch("wsgi.app.exception", (ctx,))
                        raise
                else:
                    if get_blocked():
                        _, _, content = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
                            "wsgi.block.started", (ctx, construct_url)
                        ).status_headers_content.value or (None, None, "")
                        closing_iterable = [content]
                    core.dispatch("wsgi.app.success", (ctx, closing_iterable))

            result = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
                "wsgi.request.complete", (ctx, closing_iterable, self.app_is_iterator)
            ).traced_iterable

            if stop_iteration_exception:
                if result.value:
                    # Close the request and app spans
                    result.value._finish_spans()
                    core.dispatch("wsgi.app.success", (ctx, closing_iterable))
                raise stop_iteration_exception
            return result.value if result else []

    def _traced_start_response(
        self,
        start_response: Callable,
        request_span: "Span",
        app_span: "Span",
        status: str,
        environ: dict,
        exc_info: Any = None,
    ) -> None:
        """sets the status code on a request span when start_response is called"""
        with core.context_with_data(
            "wsgi.response",
            middleware=self,
            request_span=request_span,
            parent_call=app_span,
            status=status,
            environ=environ,
            span_type=SpanTypes.WEB,
            service=trace_utils.int_service(None, self._config),
            start_span=False,
            tags={COMPONENT: self._config.integration_name, SPAN_KIND: SpanKind.SERVER},
        ):
            return start_response(status, environ, exc_info)

    def _request_span_modifier(self, req_span: "Span", environ: dict, parsed_headers: Optional[dict] = None) -> None:
        """Implement to modify span attributes on the request_span"""

    def _application_span_modifier(self, app_span: "Span", environ: dict, result: Iterable) -> None:
        """Implement to modify span attributes on the application_span"""

    def _response_span_modifier(self, resp_span: "Span", response: dict) -> None:
        """Implement to modify span attributes on the request_span"""


def construct_url(environ):
    """
    https://www.python.org/dev/peps/pep-3333/#url-reconstruction
    """
    url = environ["wsgi.url_scheme"] + "://"

    if environ.get("HTTP_HOST"):
        url += environ["HTTP_HOST"]
    else:
        url += environ["SERVER_NAME"]

        if environ["wsgi.url_scheme"] == "https":
            if environ["SERVER_PORT"] != "443":
                url += ":" + environ["SERVER_PORT"]
        else:
            if environ["SERVER_PORT"] != "80":
                url += ":" + environ["SERVER_PORT"]

    # SCRIPT_NAME composition with RAW_URI / REQUEST_URI is subtle. Two distinct
    # WSGI flows put a non-empty SCRIPT_NAME in environ:
    #   1. werkzeug DispatcherMiddleware (and similar in-process mounts) set
    #      SCRIPT_NAME to the mount prefix AND the original (pre-strip) request
    #      line is preserved in RAW_URI / REQUEST_URI. PATH_INFO is the
    #      post-strip remainder, so RAW_URI == SCRIPT_NAME + PATH_INFO.
    #      Prepending SCRIPT_NAME on top would double the mount prefix
    #      (e.g. /admin/admin/...).
    #   2. A reverse proxy (or upstream WSGI server) strips a path prefix before
    #      handing the request off, exposing only the post-strip path in
    #      RAW_URI / REQUEST_URI; PATH_INFO matches RAW_URI. SCRIPT_NAME carries
    #      the prefix that was stripped, so RAW_URI != SCRIPT_NAME + PATH_INFO
    #      (the two differ by exactly the missing prefix). Skipping SCRIPT_NAME
    #      would lose the prefix from the reported URL — including the case
    #      where the post-strip path coincidentally starts with the mount
    #      segment (public URL ``/api/api/users``, RAW_URI ``/api/users``).
    # Disambiguate by computing the canonical PEP-3333 concatenation
    # ``SCRIPT_NAME + PATH_INFO`` and comparing it against the raw URI's path
    # component. Equal → case (1), don't prepend. Different → case (2), prepend.
    # The PATH_INFO fallback path always prepends SCRIPT_NAME because PATH_INFO
    # is the stripped value in both flows.
    script_name = environ.get("SCRIPT_NAME") or ""
    path_info = environ.get("PATH_INFO") or ""
    raw = environ.get("RAW_URI") or environ.get("REQUEST_URI") or ""
    if raw:
        if script_name and not _raw_uri_already_includes_script_name(raw, script_name, path_info):
            url += quote(script_name)
        url += raw
        # on old versions of wsgi, the raw uri does not include the query string
        if environ.get("QUERY_STRING") and "?" not in raw:
            url += "?" + environ["QUERY_STRING"]
    else:
        url += quote(script_name)
        url += quote(path_info)
        if environ.get("QUERY_STRING"):
            url += "?" + environ["QUERY_STRING"]

    return url


def _raw_uri_already_includes_script_name(raw: str, script_name: str, path_info: str) -> bool:
    """Return True iff ``raw`` already includes ``script_name`` (the
    DispatcherMiddleware / in-process-mount case), so the caller should not
    prepend it a second time.

    The canonical PEP-3333 reconstruction of the request path is
    ``SCRIPT_NAME + PATH_INFO``. In an in-process mount, ``RAW_URI`` (the
    pre-strip request line) matches that concatenation. In a reverse-proxy
    strip, ``RAW_URI`` matches ``PATH_INFO`` alone (the prefix was stripped
    upstream and is not in the raw line) — even when ``PATH_INFO`` happens to
    start with the same first segment as ``SCRIPT_NAME``, which a naive
    boundary check on ``RAW_URI`` alone could not distinguish.

    ``SCRIPT_NAME == "/"`` is a degenerate root-mount form: there's no real
    prefix to inject, so we treat it as "already included" to avoid producing
    a leading double slash.

    If ``PATH_INFO`` is empty we fall back to a path-boundary check on the raw
    URI; this preserves DispatcherMiddleware correctness in legacy WSGI
    environs that don't set ``PATH_INFO``, at the cost of regressing the
    reverse-proxy-with-overlap case which is rare in practice and undetectable
    without ``PATH_INFO``.
    """
    sn = script_name.rstrip("/")
    if not sn:
        # script_name was "" (caller already gates on this) or "/" — nothing to prepend
        return True
    raw_path = raw.split("?", 1)[0].split("#", 1)[0]
    if path_info:
        # PATH_INFO and SCRIPT_NAME are WSGI-decoded strings (PEP-3333), but
        # RAW_URI / REQUEST_URI are the original percent-encoded request line.
        # Decode the raw path before comparing so benign encoding differences
        # (e.g. ``%20`` vs literal space) don't flip the decision and cause an
        # accidental double-prefix. Use ``unquote_to_bytes`` + ``latin-1`` —
        # the byte-exact lossless round-trip PEP-3333 prescribes for environ
        # strings — instead of ``unquote``'s default UTF-8/replace, which can
        # turn a stray ``%FF`` into ``�`` and silently mismatch a PATH_INFO
        # that the WSGI server passed through as latin-1.
        try:
            decoded_raw_path = unquote_to_bytes(raw_path).decode("latin-1")
        except Exception:
            decoded_raw_path = raw_path
        return decoded_raw_path == sn + path_info
    # Fallback: PATH_INFO is empty / missing. Use a path-segment-boundary check
    # — DispatcherMiddleware-correct, but the reverse-proxy-with-overlap case
    # is unrecoverable here without PATH_INFO. ``?`` and ``#`` aren't possible
    # in ``raw_path`` since we've split them off above.
    if not raw_path.startswith(sn):
        return False
    rest = raw_path[len(sn) :]
    return rest == "" or rest[0] == "/"


def get_request_headers(environ: Mapping[str, str]) -> Mapping[str, str]:
    """
    Manually grab the request headers from the environ dictionary.
    """
    request_headers: Mapping[str, str] = {}
    for key in environ.keys():
        if key.startswith("HTTP_"):
            name = from_wsgi_header(key)
            if name:
                request_headers[name] = environ[key]
    return request_headers


def default_wsgi_span_modifier(span, environ):
    span.resource = "{} {}".format(environ["REQUEST_METHOD"], environ["PATH_INFO"])


class DDWSGIMiddleware(_DDWSGIMiddlewareBase):
    """WSGI middleware providing tracing around an application.

    :param application: The WSGI application to apply the middleware to.
    :param tracer: [Deprecated] Global tracer will be used.
    :param span_modifier: "Span" modifier that can add tags to the root span.
                            Defaults to using the request method and url in the resource.
    :param app_is_iterator: Boolean indicating whether the wrapped WSGI app is a Python iterator
    """

    _request_span_name = schematize_url_operation("wsgi.request", protocol="http", direction=SpanDirection.INBOUND)
    _application_span_name = "wsgi.application"
    _response_span_name = "wsgi.response"

    def __init__(
        self,
        application: Iterable,
        tracer: Optional["Tracer"] = None,
        span_modifier: Callable[["Span", dict[str, str]], None] = default_wsgi_span_modifier,
        app_is_iterator: bool = False,
    ) -> None:
        super(DDWSGIMiddleware, self).__init__(application, tracer, config.wsgi, app_is_iterator=app_is_iterator)
        self.span_modifier = span_modifier

    def _traced_start_response(self, start_response, request_span, app_span, status, environ, exc_info=None):
        with (
            core.context_with_data(
                "wsgi.response",
                middleware=self,
                request_span=request_span,
                parent_call=app_span,
                status=status,
                environ=environ,
                span_type=SpanTypes.WEB,
                span_name="wsgi.start_response",
                service=trace_utils.int_service(None, self._config),
                start_span=True,
                tags={COMPONENT: self._config.integration_name, SPAN_KIND: SpanKind.SERVER},
                integration_config=self._config,
            ) as ctx,
            ctx.span,
        ):
            return start_response(status, environ, exc_info)

    def _request_span_modifier(self, req_span, environ, parsed_headers=None):
        url = construct_url(environ)
        request_headers = parsed_headers if parsed_headers is not None else get_request_headers(environ)
        core.dispatch("wsgi.request.prepared", (self, req_span, url, request_headers, environ))

    def _response_span_modifier(self, resp_span, response):
        core.dispatch("wsgi.response.prepared", (resp_span, response))
