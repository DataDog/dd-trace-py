import sys
from typing import TYPE_CHECKING

import ddtrace
from ddtrace import config
from ddtrace.appsec import _asm_request_context
from ddtrace.appsec import utils as appsec_utils
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection

from .. import trace_utils
from ...appsec._constants import WAF_CONTEXT_NAMES
from ...internal import core
from ...internal.compat import reraise
from ...internal.logger import get_logger
from .utils import guarantee_single_callable


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Mapping
    from typing import Optional

    from ddtrace import Span


log = get_logger(__name__)

config._add(
    "asgi",
    dict(service_name=config._get_service(default="asgi"), request_span_name="asgi.request", distributed_tracing=True),
)

ASGI_VERSION = "asgi.version"
ASGI_SPEC_VERSION = "asgi.spec_version"


def bytes_to_str(str_or_bytes):
    return str_or_bytes.decode() if isinstance(str_or_bytes, bytes) else str_or_bytes


def _extract_versions_from_scope(scope, integration_config):
    tags = {}

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


def span_from_scope(scope):
    # type: (Mapping[str, Any]) -> Optional[Span]
    """Retrieve the top-level ASGI span from the scope."""
    return scope.get("datadog", {}).get("request_spans", [None])[0]


def _request_blocked(span):
    return span and config._appsec_enabled and core.get_item(WAF_CONTEXT_NAMES.BLOCKED, span=span)


async def _blocked_asgi_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 403, "headers": []})
    await send({"type": "http.response.body", "body": b""})


class TraceMiddleware:
    """
    ASGI application middleware that traces the requests.
    Args:
        app: The ASGI application.
        tracer: Custom tracer. Defaults to the global tracer.
    """

    default_ports = {"http": 80, "https": 443, "ws": 80, "wss": 443}

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

        try:
            headers = _extract_headers(scope)
        except Exception:
            log.warning("failed to decode headers for distributed tracing", exc_info=True)
            headers = {}
        else:
            trace_utils.activate_distributed_headers(
                self.tracer, int_config=self.integration_config, request_headers=headers
            )

        ip_tuple = scope.get("client")
        if ip_tuple:
            ip = ip_tuple[0]
        else:
            ip = ""

        resource = " ".join((scope["method"], scope["path"]))
        operation_name = self.integration_config.get("request_span_name", "asgi.request")
        operation_name = schematize_url_operation(operation_name, direction=SpanDirection.INBOUND, protocol="http")

        with _asm_request_context.asm_request_context_manager(ip, headers):
            span = self.tracer.trace(
                name=operation_name,
                service=trace_utils.int_service(None, self.integration_config),
                resource=resource,
                span_type=SpanTypes.WEB,
            )

            span.set_tag_str(COMPONENT, self.integration_config.integration_name)

            # set span.kind to the type of request being performed
            span.set_tag_str(SPAN_KIND, SpanKind.SERVER)

            if "datadog" not in scope:
                scope["datadog"] = {"request_spans": [span]}
            else:
                scope["datadog"]["request_spans"].append(span)

            if self.span_modifier:
                self.span_modifier(span, scope)

            sample_rate = self.integration_config.get_analytics_sample_rate(use_global_config=True)
            if sample_rate is not None:
                span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)

            host_header = None
            for key, value in scope["headers"]:
                if key == b"host":
                    try:
                        host_header = value.decode("ascii")
                    except UnicodeDecodeError:
                        log.warning(
                            "failed to decode host header, host from http headers will not be considered", exc_info=True
                        )
                    break

            method = scope.get("method")
            server = scope.get("server")
            scheme = scope.get("scheme", "http")
            full_path = scope.get("root_path", "") + scope.get("path", "")
            if host_header:
                url = "{}://{}{}".format(scheme, host_header, full_path)
            elif server and len(server) == 2:
                port = server[1]
                default_port = self.default_ports.get(scheme, None)
                server_host = server[0] + (":" + str(port) if port is not None and port != default_port else "")
                url = "{}://{}{}".format(scheme, server_host, full_path)
            else:
                url = None

            if self.integration_config.trace_query_string:
                query_string = scope.get("query_string")
                if len(query_string) > 0:
                    query_string = bytes_to_str(query_string)
            else:
                query_string = None

            trace_utils.set_http_meta(
                span, self.integration_config, method=method, url=url, query=query_string, request_headers=headers
            )

            tags = _extract_versions_from_scope(scope, self.integration_config)
            span.set_tags(tags)

            async def wrapped_send(message):
                if span and message.get("type") == "http.response.start" and "status" in message:
                    status_code = message["status"]
                else:
                    status_code = None

                try:
                    response_headers = _extract_headers(message)
                except Exception:
                    log.warning("failed to extract response headers", exc_info=True)
                    response_headers = None

                trace_utils.set_http_meta(
                    span, self.integration_config, status_code=status_code, response_headers=response_headers
                )
                try:
                    return await send(message)
                finally:
                    # Per asgi spec, "more_body" is used if there is still data to send
                    # Close the span if "http.response.body" has no more data left to send in the
                    # response.
                    if (
                        message.get("type") == "http.response.body"
                        and not message.get("more_body", False)
                        # If the span has an error status code delay finishing the span until the
                        # traceback and exception message is available
                        and span.error == 0
                    ):
                        span.finish()

            async def wrapped_blocked_send(message):
                accept_header = (
                    "text/html"
                    if "text/html" in _asm_request_context.get_headers().get("accept", "").lower()
                    else "text/json"
                )
                if span and message.get("type") == "http.response.start":
                    message["headers"] = [
                        (
                            b"content-type",
                            b"text/html"
                            if accept_header and "text/html" in accept_header.lower()
                            else b"application/json",
                        ),
                    ]

                if message.get("type") == "http.response.body":
                    message["body"] = bytes(appsec_utils._get_blocked_template(accept_header), "utf-8")
                    message["more_body"] = False

                try:
                    return await send(message)
                finally:
                    if message.get("type") == "http.response.body" and span.error == 0:
                        span.finish()

            try:
                if _request_blocked(span):
                    return await _blocked_asgi_app(scope, receive, wrapped_blocked_send)
                return await self.app(scope, receive, wrapped_send)
            except Exception as exc:
                (exc_type, exc_val, exc_tb) = sys.exc_info()
                span.set_exc_info(exc_type, exc_val, exc_tb)
                self.handle_exception_span(exc, span)
                reraise(exc_type, exc_val, exc_tb)
            finally:
                if span in scope["datadog"]["request_spans"]:
                    scope["datadog"]["request_spans"].remove(span)
                span.finish()
