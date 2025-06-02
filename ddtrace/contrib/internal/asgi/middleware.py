import ipaddress
import os
import sys
from typing import Any
from typing import Mapping
from typing import Optional
from urllib import parse

import ddtrace
from ddtrace import config
from ddtrace.constants import _ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.asgi.utils import guarantee_single_callable
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.compat import is_valid_ip
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import get_blocked
from ddtrace.internal.utils import set_blocked
from ddtrace.internal.utils.formats import asbool
from ddtrace.trace import Span


log = get_logger(__name__)

config._add(
    "asgi",
    dict(
        service_name=config._get_service(default="asgi"),
        request_span_name="asgi.request",
        distributed_tracing=True,
        _trace_asgi_websocket=asbool(os.getenv("DD_ASGI_TRACE_WEBSOCKET", default=False)),
        # TODO: set as initially false until we gradually release feature
        _trace_asgi_websocket_messages=asbool(os.getenv("DD_TRACE_WEBSOCKET_MESSAGES_ENABLED", default=True)),
        _asgi_websockets_inherit_sampling=asbool(
            os.getenv("DD_TRACE_WEBSOCKET_MESSAGES_INHERIT_SAMPLING", default=True)
        ),
        _websocket_messages_separate=asbool(
            os.getenv("DD_TRACE_WEBSOCKET_MESSAGES_SEPARATE_TRACES", default=True)
        ),
    ),
)

ASGI_VERSION = "asgi.version"
ASGI_SPEC_VERSION = "asgi.spec_version"


def get_version() -> str:
    return ""


def bytes_to_str(str_or_bytes):
    return str_or_bytes.decode(errors="ignore") if isinstance(str_or_bytes, bytes) else str_or_bytes


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


def span_from_scope(scope: Mapping[str, Any]) -> Optional[Span]:
    """Retrieve the top-level ASGI span from the scope."""
    return scope.get("datadog", {}).get("request_spans", [None])[0]


async def _blocked_asgi_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 403, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def _parse_response_cookies(response_headers):
    cookies = {}
    try:
        result = response_headers.get("set-cookie", "").split("=", maxsplit=1)
        if len(result) == 2:
            cookie_key, cookie_value = result
            cookies[cookie_key] = cookie_value
    except Exception:
        log.debug("failed to extract response cookies", exc_info=True)
    return cookies


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
        if scope["type"] == "http":
            method = scope["method"]
        elif scope["type"] == "websocket":
            if not self.integration_config._trace_asgi_websocket:
                return await self.app(scope, receive, send)

            method = "websocket"
        else:
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
        resource = " ".join([method, scope["path"]])

        # in the case of websockets we don't currently schematize the operation names
        operation_name = self.integration_config.get("request_span_name", "asgi.request")
        if scope["type"] == "http":
            operation_name = schematize_url_operation(operation_name, direction=SpanDirection.INBOUND, protocol="http")

        pin = ddtrace.trace.Pin(service="asgi")
        pin._tracer = self.tracer

        with core.context_with_data(
            "asgi.__call__",
            remote_addr=scope.get("REMOTE_ADDR"),
            headers=headers,
            headers_case_sensitive=True,
            environ=scope,
            middleware=self,
            span_name=operation_name,
            resource=resource,
            span_type=SpanTypes.WEB,
            service=trace_utils.int_service(None, self.integration_config),
            distributed_headers=headers,
            integration_config=config.asgi,
            activate_distributed_headers=True,
            pin=pin,
        ) as ctx, ctx.span as span:
            span.set_tag_str(COMPONENT, self.integration_config.integration_name)
            ctx.set_item("req_span", span)

            span.set_tag_str(SPAN_KIND, SpanKind.SERVER)

            if scope["type"] == "websocket":
                span.set_tag_str("http.upgraded", "websocket")

            if "datadog" not in scope:
                scope["datadog"] = {"request_spans": [span]}
            else:
                scope["datadog"]["request_spans"].append(span)

            if self.span_modifier:
                self.span_modifier(span, scope)

            sample_rate = self.integration_config.get_analytics_sample_rate(use_global_config=True)
            if sample_rate is not None:
                span.set_tag(_ANALYTICS_SAMPLE_RATE_KEY, sample_rate)

            host_header = None
            for key, value in _extract_headers(scope).items():
                if key.encode() == b"host":
                    try:
                        host_header = value
                    except UnicodeDecodeError:
                        log.warning(
                            "failed to decode host header, host from http headers will not be considered", exc_info=True
                        )
                    break
            method = scope.get("method")
            server = scope.get("server")
            scheme = scope.get("scheme", "http")
            parsed_query = parse.parse_qs(bytes_to_str(scope.get("query_string", b"")))
            full_path = scope.get("path", "")
            if host_header:
                url = "{}://{}{}".format(scheme, host_header, full_path)
            elif server and len(server) == 2:
                port = server[1]
                default_port = self.default_ports.get(scheme, None)
                server_host = server[0] + (":" + str(port) if port is not None and port != default_port else "")
                url = "{}://{}{}".format(scheme, server_host, full_path)
            else:
                url = None
            query_string = scope.get("query_string")
            if query_string:
                query_string = bytes_to_str(query_string)
                if url:
                    url = f"{url}?{query_string}"
            if not self.integration_config.trace_query_string:
                query_string = None
            body = None
            result = core.dispatch_with_results("asgi.request.parse.body", (receive, headers)).await_receive_and_body
            if result:
                receive, body = await result.value

            client = scope.get("client")
            if isinstance(client, list) and len(client) and is_valid_ip(client[0]):
                peer_ip = client[0]
            else:
                peer_ip = None
            trace_utils.set_http_meta(
                span,
                self.integration_config,
                method=method,
                url=url,
                query=query_string,
                request_headers=headers,
                raw_uri=url,
                parsed_query=parsed_query,
                request_body=body,
                peer_ip=peer_ip,
                headers_are_case_sensitive=True,
            )
            tags = _extract_versions_from_scope(scope, self.integration_config)
            span.set_tags(tags)

            global_root_span = span_from_scope(scope)

            async def wrapped_send(message):
                """
                websocket.message.type
                websocket.message.length
                websocket.message.frames
                """
                try:
                    if (
                        self.integration_config._trace_asgi_websocket_messages
                        and message.get("type") == "websocket.send"
                    ):
                        with self.tracer.trace(
                            "websocket.send",
                            service=span.service,
                            resource=f"websocket {scope.get('path', '')}",
                            span_type="websocket",
                        ) as send_span:
                            send_span.set_tag_str(COMPONENT, self.integration_config.integration_name)
                            send_span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)

                            # set tags related to peer.hostname
                            client = scope.get("client")
                            if len(client) >= 1:
                                client_ip = client[0]
                                send_span.set_tag_str("out.host", client_ip)
                                try:
                                    ipaddress.ip_address(client_ip)  # validate ip address
                                    span.set_tag_str("network.client.ip", client_ip)
                                except ValueError:
                                    pass
                            # set link to http handshake span
                            # The link should have the attribute dd.kind set to resuming.
                            # If the instrumented library supports multicast or broadcast sending,
                            # there must be a link to the handshake span of every affected connection;
                            send_span.set_link(
                                trace_id=span.trace_id, span_id=span.span_id, attributes={"dd.kind": "resuming"}
                            )
                            send_span.set_metric("websocket.message.frames", 1)
                            if "text" in message:
                                send_span.set_tag_str("websocket.message.type", "text")
                                send_span.set_metric("websocket.message.length", len(message["text"].encode("utf-8")))
                            elif "binary" in message:
                                send_span.set_tag_str("websocket.message.type", "binary")
                                send_span.set_metric("websocket.message.length", len(message["bytes"]))

                            if self.integration_config._websocket_messages_separate and self.integration_config._asgi_websockets_inherit_sampling:
                                send_span.set_metric("_dd.dm.inherited", 1)

                                # if the service and resource are different
                                if send_span.service != global_root_span.service:
                                    send_span.set_tag_str("_dd.dm.service", global_root_span.service)
                                if send_span.resource != global_root_span.resource:
                                    send_span.set_tag_str("_dd.dm.resource", global_root_span.resource)

                    elif (
                        self.integration_config._trace_asgi_websocket_messages
                        and message.get("type") == "websocket.close"
                    ):
                        """
                        tags:
                        websocket.close.code
                        websocket.close.reason

                        """
                        with self.tracer.trace(
                            "websocket.close",
                            service=span.service,
                            resource=f"websocket {scope.get('path', '')}",
                            span_type="websocket",
                        ) as close_span:
                            close_span.set_tag_str(COMPONENT, self.integration_config.integration_name)
                            close_span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
                            client = scope.get("client")
                            if len(client) >= 1:
                                client_ip = client[0]
                                close_span.set_tag_str("out.host", client_ip)
                                try:
                                    ipaddress.ip_address(client_ip)  # validate ip address
                                    span.set_tag_str("network.client.ip", client_ip)
                                except ValueError:
                                    pass
                            if "text" in message:
                                close_span.set_tag_str("websocket.message.type", "text")
                                close_span.set_metric("websocket.message.length", len(message["text"].encode("utf-8")))
                            elif "binary" in message:
                                close_span.set_tag_str("websocket.message.type", "binary")
                                close_span.set_metric("websocket.message.length", len(message["bytes"]))

                            code = message.get("code")
                            reason = message.get("reason")
                            if code is not None:
                                close_span.set_metric("websocket.close.code", code)
                            if reason:
                                close_span.set_tag("websocket.close.reason", reason)
                    response_headers = _extract_headers(message)
                except Exception:
                    log.warning("failed to extract response headers", exc_info=True)
                    response_headers = None
                if span and message.get("type") == "http.response.start" and "status" in message:
                    cookies = _parse_response_cookies(response_headers)
                    status_code = message["status"]
                    trace_utils.set_http_meta(
                        span,
                        self.integration_config,
                        status_code=status_code,
                        response_headers=response_headers,
                        response_cookies=cookies,
                    )
                    core.dispatch("asgi.start_response", ("asgi",))
                core.dispatch("asgi.finalize_response", (message.get("body"), response_headers))
                blocked = get_blocked()
                if blocked:
                    raise BlockingException(blocked)
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

            async def wrapped_receive():
                """
                tags:
                websocket.message.type
                websocket.message.length
                websocket.message.frames

                _dd.dm.inherited
                _dd.dm.service
                _dd.dm.resource
                """

                try:
                    message = await receive()
                    if (
                        self.integration_config._trace_asgi_websocket_messages
                        and message["type"] == "websocket.receive"
                    ):

                        if self.integration_config._websocket_messages_separate:
                            span_context_manager = self.tracer.start_span(
                                name="websocket.receive",
                                service=span.service,
                                resource=f"websocket {scope.get('path', '')}",
                                span_type="websocket",
                                child_of=None,
                            )
                        else:
                            span_context_manager = self.tracer.trace(
                                name="websocket.receive",
                                service=span.service,
                                resource=f"websocket {scope.get('path', '')}",
                                span_type="websocket",
                            )
                        with span_context_manager as recv_span:
                            recv_span.set_tag_str(COMPONENT, self.integration_config.integration_name)
                            recv_span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
                            core.dispatch("asgi.websocket.receive", (message,))
                            # the span should have link to http handshake span
                            # the link should have attribute dd.kind set to executed_by
                            recv_span.set_link(
                                trace_id=span.trace_id, span_id=span.span_id, attributes={"dd.kind": "executed_by"}
                            )

                            if "text" in message:
                                recv_span.set_tag_str("websocket.message.type", "text")
                                recv_span.set_metric("websocket.message.length", len(message["text"].encode("utf-8")))
                            elif "binary" in message:
                                recv_span.set_tag_str("websocket.message.type", "binary")
                                recv_span.set_metric("websocket.message.length", len(message["bytes"]))

                            # since asgi is a high level framework, frames is always 1
                            recv_span.set_metric("websocket.message.frames", 1)

                            if self.integration_config._websocket_messages_separate and self.integration_config._asgi_websockets_inherit_sampling:
                                recv_span.set_metric("_dd.dm.inherited", 1)

                                if global_root_span.service != recv_span.service:
                                    recv_span.set_tag_str("_dd.dm.service", global_root_span.service)
                                if global_root_span.resource != recv_span.resource:
                                    recv_span.set_tag_str("_dd.dm.resource", global_root_span.resource)

                    elif (
                        self.integration_config._trace_asgi_websocket_messages
                        and message["type"] == "websocket.disconnect"
                    ):
                        # peer closes the connection
                        # in this case the span will be trace root (will behave like the websocket.receive use case)
                        if self.integration_config._websocket_messages_separate:
                            span_context_manager = self.tracer.start_span(
                            name="websocket.close",
                            service=span.service,
                            resource=f"websocket {scope.get('path', '')}",
                            span_type="websocket",
                            child_of=None,
                        )
                        else:
                            span_context_manager = self.tracer.trace(
                                name="websocket.close",
                                service=span.service,
                                resource=f"websocket {scope.get('path', '')}",
                                span_type="websocket",
                            )
                        
                        with span_context_manager as close_span:
                            close_span.set_tag_str(COMPONENT, self.integration_config.integration_name)
                            close_span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)

                            if self.integration_config._websocket_messages_separate and self.integration_config._asgi_websockets_inherit_sampling:
                                close_span.set_metric("_dd.dm.inherited", 1)
                                if global_root_span.service != close_span.service:
                                    close_span.set_tag_str("_dd.dm.service", global_root_span.service)
                                if global_root_span.resource != close_span.resource:
                                    close_span.set_tag_str("_dd.dm.resource", global_root_span.resource)

                            code = message.get("code")
                            reason = message.get("reason")
                            if code is not None:
                                close_span.set_metric("websocket.close.code", code)
                            if reason:
                                close_span.set_tag("websocket.close.reason", reason)
                            core.dispatch("asgi.websocket.disconnect", (message,))
                            if span and span.error == 0:
                                span.finish()
                    return message
                except Exception as exc:
                    (exc_type, exc_val, exc_tb) = sys.exc_info()
                    span.set_exc_info(exc_type, exc_val, exc_tb)
                    self.handle_exception_span(exc, span)
                    raise

            async def wrapped_blocked_send(message):
                result = core.dispatch_with_results("asgi.block.started", (ctx, url)).status_headers_content
                if result:
                    status, headers, content = result.value
                else:
                    status, headers, content = 403, [], b""
                if span and message.get("type") == "http.response.start":
                    message["headers"] = headers
                    message["status"] = int(status)
                    core.dispatch("asgi.finalize_response", (None, headers))
                elif message.get("type") == "http.response.body":
                    message["body"] = (
                        content if isinstance(content, bytes) else content.encode("utf-8", errors="ignore")
                    )
                    message["more_body"] = False
                    core.dispatch("asgi.finalize_response", (content, None))
                try:
                    result = await send(message)
                finally:
                    trace_utils.set_http_meta(
                        span, self.integration_config, status_code=status, response_headers=headers
                    )
                    if message.get("type") == "http.response.body" and span.error == 0:
                        span.finish()

            wrapped_recv = wrapped_receive if scope["type"] == "websocket" else receive

            try:
                core.dispatch("asgi.start_request", ("asgi",))
                # Do not block right here. Wait for route to be resolved in starlette/patch.py
                return await self.app(scope, wrapped_recv, wrapped_send)
            except BlockingException as e:
                set_blocked(e.args[0])
                return await _blocked_asgi_app(scope, receive, wrapped_blocked_send)
            except Exception as exc:
                (exc_type, exc_val, exc_tb) = sys.exc_info()
                span.set_exc_info(exc_type, exc_val, exc_tb)
                self.handle_exception_span(exc, span)
                raise
            except BaseException as exception:
                # managing python 3.11+ BaseExceptionGroup with compatible code for 3.10 and below
                if exception.__class__.__name__ == "BaseExceptionGroup":
                    for exc in exception.exceptions:
                        if isinstance(exc, BlockingException):
                            set_blocked(exc.args[0])
                            return await _blocked_asgi_app(scope, receive, wrapped_blocked_send)
                raise
            finally:
                core.dispatch("web.request.final_tags", (span,))
                if span in scope["datadog"]["request_spans"]:
                    scope["datadog"]["request_spans"].remove(span)
