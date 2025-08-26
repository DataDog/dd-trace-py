from functools import wraps
import os
import sys
from typing import Any
from typing import Callable
from typing import Mapping
from typing import Optional
from typing import Union
from urllib import parse

import ddtrace
from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.asgi.utils import guarantee_single_callable
from ddtrace.contrib.internal.trace_utils import _copy_trace_level_tags
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanLinkKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.ext import websocket
from ddtrace.ext.net import TARGET_HOST
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal._exceptions import find_exception
from ddtrace.internal.compat import is_valid_ip
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import SAMPLING_DECISION_MAKER_INHERITED
from ddtrace.internal.constants import SAMPLING_DECISION_MAKER_RESOURCE
from ddtrace.internal.constants import SAMPLING_DECISION_MAKER_SERVICE
from ddtrace.internal.constants import SPAN_LINK_KIND
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import get_blocked
from ddtrace.internal.utils import set_blocked
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings._config import _get_config
from ddtrace.trace import Span


log = get_logger(__name__)

if os.getenv("DD_ASGI_TRACE_WEBSOCKET") is not None:
    log.warning(
        "DD_ASGI_TRACE_WEBSOCKET is deprecated and will be removed in a future version. "
        "Use DD_TRACE_WEBSOCKET_MESSAGES_ENABLED instead."
    )

config._add(
    "asgi",
    dict(
        service_name=config._get_service(default="asgi"),
        request_span_name="asgi.request",
        distributed_tracing=True,
        trace_asgi_websocket_messages=_get_config(
            "DD_TRACE_WEBSOCKET_MESSAGES_ENABLED",
            default=_get_config("DD_ASGI_TRACE_WEBSOCKET", default=False, modifier=asbool),
            modifier=asbool,
        ),
        asgi_websocket_messages_inherit_sampling=asbool(
            _get_config("DD_TRACE_WEBSOCKET_MESSAGES_INHERIT_SAMPLING", default=True)
        )
        and asbool(_get_config("DD_TRACE_WEBSOCKET_MESSAGES_SEPARATE_TRACES", default=True)),
        websocket_messages_separate_traces=asbool(
            _get_config("DD_TRACE_WEBSOCKET_MESSAGES_SEPARATE_TRACES", default=True)
        ),
        obfuscate_404_resource=asbool(_get_config("DD_ASGI_OBFUSCATE_404_RESOURCE", default=False)),
    ),
)

ASGI_VERSION = "asgi.version"
ASGI_SPEC_VERSION = "asgi.spec_version"


def get_version() -> str:
    return ""


def bytes_to_str(str_or_bytes: Union[str, bytes]) -> str:
    return str_or_bytes.decode(errors="ignore") if isinstance(str_or_bytes, bytes) else str_or_bytes


def _extract_versions_from_scope(scope: Mapping[str, Any], integration_config: Mapping[str, Any]) -> Mapping[str, Any]:
    """
    Extract HTTP and ASGI version information from scope.
    """
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


def _extract_headers(scope: Mapping[str, Any]) -> Mapping[str, Any]:
    """
    Extract and decode headers from ASGI scope.

    ASGI headers are stored as byte strings; this method decodes them
    to UTF-8 strings for easier processing.
    """
    headers = scope.get("headers")
    if headers:
        # headers: (Iterable[[byte string, byte string]])
        return dict((bytes_to_str(k), bytes_to_str(v)) for (k, v) in headers)
    return {}


def _default_handle_exception_span(exc, span):
    """Default handler for exception for span"""
    span.set_tag(http.STATUS_CODE, 500)


def span_from_scope(scope: Mapping[str, Any]) -> Optional[Span]:
    return scope.get("datadog", {}).get("request_spans", [None])[0]


async def _blocked_asgi_app(scope: Mapping[str, Any], receive: Callable, send: Callable):
    await send({"type": "http.response.start", "status": 403, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def _parse_response_cookies(response_headers: Mapping[str, str]) -> Mapping[str, Any]:
    cookies = {}
    try:
        result = response_headers.get("set-cookie", "").split("=", maxsplit=1)
        if len(result) == 2:
            cookie_key, cookie_value = result
            cookies[cookie_key] = cookie_value
    except Exception:
        log.debug("failed to extract response cookies", exc_info=True)
    return cookies


def _inherit_sampling_tags(span: Span, source: Span):
    span.set_metric(SAMPLING_DECISION_MAKER_INHERITED, 1)
    span.set_tag_str(SAMPLING_DECISION_MAKER_SERVICE, source.service)
    span.set_tag_str(SAMPLING_DECISION_MAKER_RESOURCE, source.resource)


def _set_message_tags_on_span(websocket_span: Span, message: Mapping[str, Any]):
    if "text" in message:
        websocket_span.set_tag_str(websocket.MESSAGE_TYPE, "text")
        websocket_span.set_metric(websocket.MESSAGE_LENGTH, len(message["text"].encode("utf-8")))
    elif "binary" in message:
        websocket_span.set_tag_str(websocket.MESSAGE_TYPE, "binary")
        websocket_span.set_metric(websocket.MESSAGE_LENGTH, len(message["bytes"]))


def _set_client_ip_tags(scope: Mapping[str, Any], span: Span):
    client = scope.get("client")
    if len(client) >= 1:
        client_ip = client[0]
        span.set_tag_str(TARGET_HOST, client_ip)
        try:
            is_valid_ip(client_ip)
            span.set_tag_str("network.client.ip", client_ip)
        except ValueError as e:
            log.debug("Could not validate client IP address for websocket send message: %s", str(e))


def _set_websocket_close_tags(span: Span, message: Mapping[str, Any]):
    code = message.get("code")
    reason = message.get("reason")
    if code is not None:
        span.set_metric(websocket.CLOSE_CODE, code)
    if reason:
        span.set_tag(websocket.CLOSE_REASON, reason)


def _cleanup_previous_receive(scope: Mapping[str, Any]):
    current_receive_span = scope.get("datadog", {}).get("current_receive_span")
    if current_receive_span:
        current_receive_span.finish()
        scope["datadog"].pop("current_receive_span", None)


class TraceMiddleware:
    """
    ASGI application middleware that traces HTTP and websocket requests.

    Provides distributed tracing for ASGI applications, including
    support for websocket connections with configurable message-level tracing.

    When websocket instrumentation is enabled, the middleware creates spans for:
    - websocket handshake (HTTP upgrade)
    - Individual message receive/send operations
    - Connection close events
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

    async def __call__(self, scope: Mapping[str, Any], receive: Callable, send: Callable):
        """
        Handle ASGI application calls with tracing.

        Processes ASGI requests and responses, creating spans for:
        - HTTP requests and responses
        - websocket handshakes and message exchanges
        - Error handling and exception tracking

        Raises:
            BlockingException: When request is blocked
            Exception: Re-raises any exceptions from the wrapped application

        Span Types Created:
            - HTTP: asgi.request spans with HTTP metadata
            - websocket: websocket.receive, websocket.send, websocket.close spans
        """
        if scope["type"] == "http":
            method = scope["method"]
        elif scope["type"] == "websocket" and self.integration_config.trace_asgi_websocket_messages:
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

            @wraps(receive)
            async def wrapped_receive():
                """
                Wrapped receive function that instruments websocket message reception.

                Intercepts websocket receive operations to create ExecutionContexts for:
                - Message receive timing and metadata
                - Message type (text/binary)
                - Message length measurement
                - Connection close events

                When websocket instrumentation is enabled, creates ExecutionContexts for:
                - websocket.receive: For incoming messages
                - websocket.close: For peer disconnect events

                Note:
                    - Spans are linked to the handshake span
                    - Receive spans are finished exactly when the next receive operation starts
                """

                if not self.integration_config.trace_asgi_websocket_messages:
                    return await receive()

                current_receive_span = scope.get("datadog", {}).get("current_receive_span")

                with core.context_with_data(
                    "asgi.websocket.receive_message",
                    tracer=self.tracer,
                    integration_config=self.integration_config,
                    span_name="websocket.receive",
                    service=span.service,
                    resource=f"websocket {scope.get('path', '')}",
                    span_type=SpanTypes.WEBSOCKET,
                    child_of=span if not self.integration_config.websocket_messages_separate_traces else None,
                    call_trace=False,
                    activate=True,
                ) as ctx:
                    if current_receive_span:
                        current_receive_span.finish()
                        scope["datadog"].pop("current_receive_span", None)

                    recv_span = ctx.span
                    try:
                        message = await receive()

                        if scope["type"] == "websocket" and message["type"] == "websocket.receive":
                            self._handle_websocket_receive_message(scope, message, recv_span, span)

                        elif message["type"] == "websocket.disconnect":
                            self._handle_websocket_disconnect_message(scope, message, span)

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
            async def wrapped_send(message: Mapping[str, Any]):
                """
                Wrapped ASGI send function that traces websocket message transmission.

                Intercepts websocket send operations to create spans for:
                - Message transmission timing and metadata
                - Message type (text/binary)
                - Message length measurement
                - Connection close operations

                When websocket instrumentation is enabled, creates ExecutionContexts for:
                - websocket.send: For outgoing messages
                - websocket.close: For application-initiated close events

                Note:
                    - Spans are linked to the handshake span
                    - Context (baggage, sampling) is propagated from handshake span
                    - Each sent message is attached to the current context
                    - Close spans include additional metadata like close codes and reasons
                """
                try:
                    if (
                        scope["type"] == "websocket"
                        and self.integration_config.trace_asgi_websocket_messages
                        and message.get("type") == "websocket.accept"
                    ):
                        # Close handshake span once connection is upgraded
                        if span and span.error == 0:
                            span.finish()
                        return await send(message)

                    elif (
                        scope["type"] == "websocket"
                        and self.integration_config.trace_asgi_websocket_messages
                        and message.get("type") == "websocket.send"
                    ):
                        self._handle_websocket_send_message(scope, message, span)

                    elif (
                        scope["type"] == "websocket"
                        and self.integration_config.trace_asgi_websocket_messages
                        and message.get("type") == "websocket.close"
                    ):
                        self._handle_websocket_close_message(scope, message, span)
                        return await send(message)

                    response_headers = _extract_headers(message)
                except Exception:
                    log.warning("failed to extract response headers", exc_info=True)
                    response_headers = None
                self._handle_http_response(scope, message, span, method, response_headers)
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
                        # If the span has an error status code delay finishing the span until the
                        # traceback and exception message is available
                        span.finish()

            async def wrapped_blocked_send(message: Mapping[str, Any]):
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
                if exc := find_exception(exception, BlockingException):
                    set_blocked(exc.args[0])
                    return await _blocked_asgi_app(scope, receive, wrapped_blocked_send)
                raise
            finally:
                core.dispatch("web.request.final_tags", (span,))
                if span in scope["datadog"]["request_spans"]:
                    scope["datadog"]["request_spans"].remove(span)

                # Safety mechanism: finish any remaining receive spans to ensure no spans are unfinished
                if scope["type"] == "websocket" and "datadog" in scope:
                    _cleanup_previous_receive(scope)

    def _handle_websocket_send_message(self, scope: Mapping[str, Any], message: Mapping[str, Any], request_span: Span):
        current_receive_span = scope.get("datadog", {}).get("current_receive_span")

        if self.integration_config.websocket_messages_separate_traces and current_receive_span:
            parent_span = current_receive_span
        else:
            parent_span = request_span

        with core.context_with_data(
            "asgi.websocket.send_message",
            tracer=self.tracer,
            integration_config=self.integration_config,
            span_name="websocket.send",
            service=request_span.service,
            resource=f"websocket {scope.get('path', '')}",
            span_type=SpanTypes.WEBSOCKET,
            child_of=parent_span,
            call_trace=False,
            activate=True,
            tags={COMPONENT: self.integration_config.integration_name, SPAN_KIND: SpanKind.PRODUCER},
        ) as ctx, ctx.span as send_span:
            # Propagate context from the handshake span (baggage, origin, sampling decision)
            # set tags related to peer.hostname
            _set_client_ip_tags(scope, send_span)

            send_span.set_link(
                trace_id=request_span.trace_id,
                span_id=request_span.span_id,
                attributes={SPAN_LINK_KIND: SpanLinkKind.RESUMING},
            )
            send_span.set_metric(websocket.MESSAGE_FRAMES, 1)
            _set_message_tags_on_span(send_span, message)

    def _handle_websocket_close_message(self, scope: Mapping[str, Any], message: Mapping[str, Any], request_span: Span):
        current_receive_span = scope.get("datadog", {}).get("current_receive_span")

        # Use receive span as parent if it exists, otherwise use main span
        if self.integration_config.websocket_messages_separate_traces and current_receive_span:
            parent_span = current_receive_span
        else:
            parent_span = request_span

        with core.context_with_data(
            "asgi.websocket.close_message",
            tracer=self.tracer,
            integration_config=self.integration_config,
            span_name="websocket.close",
            resource=f"websocket {scope.get('path', '')}",
            span_type=SpanTypes.WEBSOCKET,
            child_of=parent_span,
            call_trace=False,
            activate=True,
            tags={COMPONENT: self.integration_config.integration_name, SPAN_KIND: SpanKind.PRODUCER},
        ) as ctx, ctx.span as close_span:
            _set_client_ip_tags(scope, close_span)

            _set_message_tags_on_span(close_span, message)
            # should act like send span (outgoing message) case
            close_span.set_link(
                trace_id=request_span.trace_id,
                span_id=request_span.span_id,
                attributes={SPAN_LINK_KIND: SpanLinkKind.RESUMING},
            )
            _copy_trace_level_tags(close_span, request_span)

            _set_websocket_close_tags(close_span, message)

        _cleanup_previous_receive(scope)

    def _handle_http_response(
        self,
        scope: Mapping[str, Any],
        message: Mapping[str, Any],
        span: Span,
        method: str,
        response_headers: Optional[Mapping[str, Any]],
    ):
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

    def _handle_websocket_receive_message(
        self, scope: Mapping[str, Any], message: Mapping[str, Any], recv_span: Span, request_span: Span
    ):
        recv_span.set_tag_str(COMPONENT, self.integration_config.integration_name)
        recv_span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
        recv_span.set_tag_str(websocket.RECEIVE_DURATION_TYPE, "blocking")

        _set_message_tags_on_span(recv_span, message)

        recv_span.set_metric(websocket.MESSAGE_FRAMES, 1)

        recv_span.set_link(
            trace_id=request_span.trace_id,
            span_id=request_span.span_id,
            attributes={SPAN_LINK_KIND: SpanLinkKind.EXECUTED},
        )

        if self.integration_config.asgi_websocket_messages_inherit_sampling:
            _inherit_sampling_tags(recv_span, request_span._local_root)

        _copy_trace_level_tags(recv_span, request_span)

        scope["datadog"]["current_receive_span"] = recv_span

    def _handle_websocket_disconnect_message(
        self, scope: Mapping[str, Any], message: Mapping[str, Any], request_span: Span
    ):
        _cleanup_previous_receive(scope)

        # Create the span when the handler is invoked (when receive() is called)
        with core.context_with_data(
            "asgi.websocket.disconnect_message",
            tracer=self.tracer,
            integration_config=self.integration_config,
            span_name="websocket.close",
            service=request_span.service,
            resource=f"websocket {scope.get('path', '')}",
            span_type=SpanTypes.WEBSOCKET,
            child_of=request_span if not self.integration_config.websocket_messages_separate_traces else None,
            call_trace=False,
            activate=True,
            tags={COMPONENT: self.integration_config.integration_name, SPAN_KIND: SpanKind.CONSUMER},
        ) as ctx, ctx.span as disconnect_span:
            disconnect_span.set_link(
                trace_id=request_span.trace_id,
                span_id=request_span.span_id,
                attributes={SPAN_LINK_KIND: SpanLinkKind.EXECUTED},
            )

            if self.integration_config.asgi_websocket_messages_inherit_sampling:
                _inherit_sampling_tags(disconnect_span, request_span._local_root)

            _copy_trace_level_tags(disconnect_span, request_span)
            _set_websocket_close_tags(disconnect_span, message)
