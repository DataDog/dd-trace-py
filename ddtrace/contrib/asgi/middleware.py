import sys

import ddtrace
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.ext import SpanTypes, http
from ddtrace.http import store_request_headers, store_response_headers
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.settings import config

from ...compat import reraise
from ...internal.logger import get_logger
from .utils import guarantee_single_callable
from .. import trace_utils

log = get_logger(__name__)

config._add(
    "asgi",
    dict(service_name=config._get_service(default="asgi"), request_span_name="asgi.request", distributed_tracing=True),
)

ASGI_VERSION = "asgi.version"
ASGI_SPEC_VERSION = "asgi.spec_version"


def bytes_to_str(str_or_bytes):
    return str_or_bytes.decode() if isinstance(str_or_bytes, bytes) else str_or_bytes


def _extract_tags_from_scope(scope, integration_config):
    tags = {}

    if integration_config.trace_query_string:
        query_string = scope.get("query_string")
        if len(query_string) > 0:
            tags[http.QUERY_STRING] = bytes_to_str(query_string)
    else:
        query_string = None

    http_version = scope.get("http_version")
    if http_version:
        tags[http.VERSION] = http_version

    scope_asgi = scope.get("asgi")

    if scope_asgi and "version" in scope_asgi:
        tags[ASGI_VERSION] = scope_asgi["version"]

    if scope_asgi and "spec_version" in scope_asgi:
        tags[ASGI_SPEC_VERSION] = scope_asgi["spec_version"]

    return tags


def _extract_headers(scope):
    headers = scope.get("headers")
    if headers:
        # headers: (Iterable[[byte string, byte string]])
        return dict((bytes_to_str(k), bytes_to_str(v)) for (k, v) in headers)
    return {}


def _default_handle_exception_span(exc, span):
    """Default handler for exception for span"""
    span.set_tag(http.STATUS_CODE, 500)


class TraceMiddleware:
    """
    ASGI application middleware that traces the requests.

    Args:
        app: The ASGI application.
        tracer: Custom tracer. Defaults to the global tracer.
    """

    def __init__(
        self,
        app,
        tracer=None,
        integration_config=config.asgi,
        handle_exception_span=_default_handle_exception_span,
        span_modifier=None,
    ):
        self.app = guarantee_single_callable(app)
        self.tracer = tracer or ddtrace.tracer
        self.integration_config = integration_config
        self.handle_exception_span = handle_exception_span
        self.span_modifier = span_modifier

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = _extract_headers(scope)

        if self.integration_config.distributed_tracing:
            propagator = HTTPPropagator()
            context = propagator.extract(headers)
            if context.trace_id:
                self.tracer.context_provider.activate(context)

        resource = "{} {}".format(scope["method"], scope["path"])

        span = self.tracer.trace(
            name=self.integration_config.get("request_span_name", "asgi.request"),
            service=trace_utils.int_service(None, self.integration_config),
            resource=resource,
            span_type=SpanTypes.WEB,
        )

        if self.span_modifier:
            self.span_modifier(span, scope)

        sample_rate = self.integration_config.get_analytics_sample_rate(use_global_config=True)
        if sample_rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)

        method = scope.get("method")
        server = scope.get("server")
        url = None
        if server and len(server) == 2:
            port = server[1]
            server_host = server[0] + (":" + str(port) if port is not None and port != 80 else "")
            full_path = scope.get("root_path", "") + scope.get("path", "")
            url = scope.get("scheme", "http") + "://" + server_host + full_path

        trace_utils.set_http_meta(self.integration_config, span, method=method, url=url)

        tags = _extract_tags_from_scope(scope, self.integration_config)
        span.set_tags(tags)

        store_request_headers(headers, span, self.integration_config)

        async def wrapped_send(message):
            status_code = None
            if span and message.get("type") == "http.response.start":
                if "status" in message:
                    status_code = message["status"]
            trace_utils.set_http_meta(self.integration_config, span, status_code=status_code)

            if "headers" in message:
                store_response_headers(message["headers"], span, self.integration_config)

            return await send(message)

        try:
            return await self.app(scope, receive, wrapped_send)
        except Exception as exc:
            (exc_type, exc_val, exc_tb) = sys.exc_info()
            span.set_exc_info(exc_type, exc_val, exc_tb)
            self.handle_exception_span(exc, span)
            reraise(exc_type, exc_val, exc_tb)
        finally:
            span.finish()
