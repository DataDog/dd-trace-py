from functools import wraps
import ipaddress
import os
import sys
from typing import Any
from typing import Mapping
from typing import Optional
from urllib import parse

import ddtrace
from ddtrace import config
from ddtrace.constants import _ORIGIN_KEY
from ddtrace.constants import _SAMPLING_DECISION_MAKER
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
from ddtrace.settings._config import _get_config
from ddtrace.trace import Span


log = get_logger(__name__)

config._add(
    "asgi",
    dict(
        service_name=config._get_service(default="asgi"),
        request_span_name="asgi.request",
        distributed_tracing=True,
        _trace_asgi_websocket_messages=asbool(os.getenv("DD_TRACE_WEBSOCKET_MESSAGES_ENABLED", default=False)),
        _asgi_websockets_inherit_sampling=asbool(
            os.getenv("DD_TRACE_WEBSOCKET_MESSAGES_INHERIT_SAMPLING", default=True)
        )
        and asbool(os.getenv("DD_TRACE_WEBSOCKET_MESSAGES_SEPARATE_TRACES", default=True)),
        _websocket_messages_separate_traces=asbool(
            os.getenv("DD_TRACE_WEBSOCKET_MESSAGES_SEPARATE_TRACES", default=True)
        ),
        obfuscate_404_resource=asbool(_get_config("DD_ASGI_OBFUSCATE_404_RESOURCE", default=False)),
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


async def _blocked_asgi_app(scope, receive, send, ctx=None, url=None):
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
            if not self.integration_config._trace_asgi_websocket_messages:
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
            # Ensure HTTP requests always have the correct scheme
            if scope["type"] == "http":
                scheme = "http"
            else:
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
            global_root_span = span._local_root or span

            def _set_sampling_inherited(span: Span):
                span.set_metric("_dd.dm.inherited", 1)
                span.set_tag_str("_dd.dm.service", global_root_span.service)
                span.set_tag_str("_dd.dm.resource", global_root_span.resource)

            def _copy_baggage_and_sampling(websocket_span):
                if span.context:
                    for key, value in span.context._baggage.items():
                        websocket_span.context.set_baggage_item(key, value)
                        websocket_span.set_tag_str(f"baggage.{key}", value)

                    if span.context.sampling_priority is not None:
                        websocket_span.context.sampling_priority = span.context.sampling_priority

                    if span.context._meta.get(_ORIGIN_KEY):
                        websocket_span.set_tag_str(_ORIGIN_KEY, span.context._meta[_ORIGIN_KEY])

                    if span.context._meta.get(_SAMPLING_DECISION_MAKER):
                        websocket_span.set_tag_str(
                            _SAMPLING_DECISION_MAKER, span.context._meta[_SAMPLING_DECISION_MAKER]
                        )

            def _set_message_tags_on_span(websocket_span, message):
                if "text" in message:
                    websocket_span.set_tag_str("websocket.message.type", "text")
                    websocket_span.set_metric("websocket.message.length", len(message["text"].encode("utf-8")))
                elif "binary" in message:
                    websocket_span.set_tag_str("websocket.message.type", "binary")
                    websocket_span.set_metric("websocket.message.length", len(message["bytes"]))

            @wraps(receive)
            async def wrapped_receive():
                """
                websocket.message.type
                websocket.message.length
                websocket.message.frames
                """

                if not self.integration_config._trace_asgi_websocket_messages:
                    return await receive()

                # Create the span when the handler is invoked (when receive() is called)
                # This measures the time from receive() call to message processing completion
                recv_span = self.tracer.start_span(
                    name="websocket.receive",
                    service=span.service,
                    resource=f"websocket {scope.get('path', '')}",
                    span_type="websocket",
                    child_of=span if not self.integration_config._websocket_messages_separate_traces else None,
                    activate=True,
                )
                try:
                    message = await receive()

                    if scope["type"] == "websocket" and message["type"] == "websocket.receive":
                        # Keep the current receive span open to measure processing time
                        # It will be finished after the application processes the message

                        # Finish the previous receive span before creating a new one
                        current_receive_span = scope.get("datadog", {}).get("current_receive_span")
                        if current_receive_span:
                            current_receive_span.finish()
                            scope["datadog"].pop("current_receive_span", None)

                        core.dispatch("asgi.websocket.receive", (message,))
                        recv_span.set_tag_str(COMPONENT, self.integration_config.integration_name)
                        recv_span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)

                        _set_message_tags_on_span(recv_span, message)

                        recv_span.set_metric("websocket.message.frames", 1)

                        recv_span.set_link(
                            trace_id=span.trace_id, span_id=span.span_id, attributes={"dd.kind": "executed_by"}
                        )

                        # set sampling
                        if self.integration_config._asgi_websockets_inherit_sampling:
                            _set_sampling_inherited(recv_span)

                        _copy_baggage_and_sampling(recv_span)

                        # Store the receive span in scope so it can be used by send operations
                        if "datadog" not in scope:
                            scope["datadog"] = {}

                        # Store the receive span to be finished after processing message
                        scope["datadog"]["current_receive_span"] = recv_span

                    elif message["type"] == "websocket.disconnect":
                        # Peer closes the connection: create a new trace root
                        # This should behave like the websocket.receive use case

                        # Clean any existing receive span before handling peer disconnect
                        current_receive_span = scope.get("datadog", {}).get("current_receive_span")
                        if current_receive_span:
                            current_receive_span.finish()
                            scope["datadog"].pop("current_receive_span", None)

                        disconnect_span = self.tracer.start_span(
                            name="websocket.close",
                            service=span.service,
                            resource=f"websocket {scope.get('path', '')}",
                            span_type="websocket",
                            child_of=span if not self.integration_config._websocket_messages_separate_traces else None,
                        )

                        disconnect_span.set_tag_str(COMPONENT, self.integration_config.integration_name)
                        disconnect_span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)

                        disconnect_span.set_link(
                            trace_id=span.trace_id, span_id=span.span_id, attributes={"dd.kind": "executed_by"}
                        )

                        # set sampling
                        if self.integration_config._asgi_websockets_inherit_sampling:
                            _set_sampling_inherited(disconnect_span)

                        _copy_baggage_and_sampling(disconnect_span)

                        code = message.get("code")
                        reason = message.get("reason")
                        if code is not None:
                            disconnect_span.set_metric("websocket.close.code", code)
                        if reason:
                            disconnect_span.set_tag("websocket.close.reason", reason)

                        core.dispatch("asgi.websocket.disconnect", (message,))

                        disconnect_span.finish()

                        if span and span.error == 0:
                            span.finish()

                    return message
                except Exception:
                    recv_span.set_exc_info(*sys.exc_info())
                    recv_span.finish()
                    raise
                finally:
                    # Ensure the recv_span is finished if it wasn't stored in scope
                    # handles cases where the message type is not websocket.receive or websocket.disconnect
                    if "datadog" not in scope or "current_receive_span" not in scope["datadog"]:
                        recv_span.finish()

            @wraps(send)
            async def wrapped_send(message):
                """
                websocket.message.type
                websocket.message.length
                websocket.message.frames
                """
                try:
                    if (
                        scope["type"] == "websocket"
                        and self.integration_config._trace_asgi_websocket_messages
                        and message.get("type") == "websocket.send"
                    ):
                        current_receive_span = scope.get("datadog", {}).get("current_receive_span")

                        if self.integration_config._websocket_messages_separate_traces and current_receive_span:
                            parent_span = current_receive_span
                        else:
                            parent_span = span

                        with self.tracer.start_span(
                            name="websocket.send",
                            service=span.service,
                            resource=f"websocket {scope.get('path', '')}",
                            span_type="websocket",
                            child_of=parent_span,
                            activate=True,
                        ) as send_span:
                            send_span.set_tag_str(COMPONENT, self.integration_config.integration_name)
                            send_span.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)

                            # Propagate context from the handshake span (baggage, origin, sampling decision)
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
                            send_span.set_link(
                                trace_id=span.trace_id, span_id=span.span_id, attributes={"dd.kind": "resuming"}
                            )
                            send_span.set_metric("websocket.message.frames", 1)
                            _set_message_tags_on_span(send_span, message)

                        # Finish the receive span after processing the message and sending response
                        current_receive_span = scope.get("datadog", {}).get("current_receive_span")
                        if current_receive_span:
                            current_receive_span.finish()
                            scope["datadog"].pop("current_receive_span", None)

                    elif (
                        scope["type"] == "websocket"
                        and self.integration_config._trace_asgi_websocket_messages
                        and message.get("type") == "websocket.close"
                    ):
                        """
                        tags:
                        websocket.close.code
                        websocket.close.reason

                        """
                        current_receive_span = scope.get("datadog", {}).get("current_receive_span")

                        # Use receive span as parent if it exists, otherwise use main span
                        if current_receive_span:
                            parent_span = current_receive_span
                        else:
                            parent_span = span

                        with self.tracer.start_span(
                            name="websocket.close",
                            service=span.service,
                            resource=f"websocket {scope.get('path', '')}",
                            span_type="websocket",
                            child_of=parent_span,
                            activate=True,
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

                            _set_message_tags_on_span(close_span, message)
                            # should act like send span (outgoing message) case
                            close_span.set_link(
                                trace_id=span.trace_id, span_id=span.span_id, attributes={"dd.kind": "resuming"}
                            )
                            _copy_baggage_and_sampling(close_span)

                            code = message.get("code")
                            reason = message.get("reason")
                            if code is not None:
                                close_span.set_metric("websocket.close.code", code)
                            if reason:
                                close_span.set_tag("websocket.close.reason", reason)

                        current_receive_span = scope.get("datadog", {}).get("current_receive_span")
                        if current_receive_span:
                            current_receive_span.finish()
                            scope["datadog"].pop("current_receive_span", None)

                        return await send(message)

                    response_headers = _extract_headers(message)
                except Exception:
                    log.warning("failed to extract response headers", exc_info=True)
                    response_headers = None
                if span and message.get("type") == "http.response.start" and "status" in message:
                    cookies = _parse_response_cookies(response_headers)
                    status_code = message["status"]
                    if self.integration_config.obfuscate_404_resource and status_code == 404:
                        span.resource = " ".join((method, "404"))

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
                        and span.error == 0
                    ):
                        span.finish()

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
                    return await send(message)
                finally:
                    trace_utils.set_http_meta(
                        span, self.integration_config, status_code=status, response_headers=headers
                    )
                    if message.get("type") == "http.response.body" and span.error == 0:
                        span.finish()

            wrapped_recv = wrapped_receive if scope["type"] == "websocket" else receive
            try:
                core.dispatch("asgi.start_request", ("asgi",))

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

                # Safety mechanism: finish any remaining receive spans to ensure no spans are unfinished
                if scope["type"] == "websocket" and "datadog" in scope:
                    current_receive_span = scope["datadog"].get("current_receive_span")
                    if current_receive_span:
                        current_receive_span.finish()
                        scope["datadog"].pop("current_receive_span", None)
