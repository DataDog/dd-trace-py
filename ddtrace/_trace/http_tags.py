"""HTTP span tag helpers that write either the Datadog or the OTel attribute, depending on the active semantics."""

from typing import Optional

from ddtrace._trace.otel.http.resource import INSTRUMENTATION_HTTP_RESOURCE
from ddtrace._trace.otel.http.resource import RESOURCE_SET_BY_USER
from ddtrace._trace.otel.http.tags import _set_otel_query
from ddtrace._trace.span import Span
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.settings._config import config


def set_client_address_tags(
    span: Span, client_address: Optional[str], network_peer_address: Optional[str] = None
) -> None:
    if config._otel_trace_semantics_enabled:
        if client_address:
            span._set_attribute(otel_http.CLIENT_ADDRESS, client_address)
        if network_peer_address:
            span._set_attribute(otel_http.NETWORK_PEER_ADDRESS, network_peer_address)
    else:
        if client_address:
            span._set_attribute(http.CLIENT_IP, client_address)
        if network_peer_address:
            span._set_attribute(net.CLIENT_IP, network_peer_address)


def set_query_string_tag(span: Span, query: str) -> None:
    if config._otel_trace_semantics_enabled:
        _set_otel_query(span, query)
    else:
        span._set_attribute(http.QUERY_STRING, query)


def set_instrumentation_resource(span: Span, resource: str) -> None:
    """Set an instrumentation-owned resource without overriding a user replacement."""
    if config._otel_trace_semantics_enabled:
        if span._get_ctx_item(RESOURCE_SET_BY_USER):
            return

        previous_resource = span._get_ctx_item(INSTRUMENTATION_HTTP_RESOURCE)
        if previous_resource is not None and span.resource != previous_resource:
            span._set_ctx_item(RESOURCE_SET_BY_USER, True)
            return

    span.resource = resource
    if config._otel_trace_semantics_enabled:
        span._set_ctx_item(INSTRUMENTATION_HTTP_RESOURCE, resource)
