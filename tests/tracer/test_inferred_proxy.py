import time

import pytest

from ddtrace._trace._inferred_proxy import INFERRED_SPAN_NAMES
from ddtrace._trace._inferred_proxy import create_inferred_proxy_span_if_headers_exist
from ddtrace._trace.span import Span
from ddtrace.internal.core import ExecutionContext


@pytest.mark.parametrize("missing_resource_path", [False, True])
@pytest.mark.parametrize(
    "proxy_header,span_name", [("aws-httpapi", "aws.httpapi"), ("aws-apigateway", "aws.apigateway")]
)
def test_create_inferred_proxy_span_for_apigateway(
    proxy_header,
    span_name,
    missing_resource_path,
    tracer,
) -> None:
    ctx = ExecutionContext("test")
    headers = {
        "x-dd-proxy": proxy_header,
        "x-dd-proxy-request-time-ms": "1736973768000",
        "x-dd-proxy-path": "/http-api-path",
        "x-dd-proxy-httpmethod": "POST",
        "x-dd-proxy-domain-name": "id.execute-api.us-east-1.amazonaws.com",
        "x-dd-proxy-stage": "prod",
        "x-dd-proxy-account-id": "123456789012",
        "x-dd-proxy-api-id": "abcdef123456",
        "x-dd-proxy-region": "us-east-1",
        "x-dd-proxy-user": "apigw-user",
    }

    if not missing_resource_path:
        headers["x-dd-proxy-resource-path"] = "/{Path}"

    create_inferred_proxy_span_if_headers_exist(ctx, headers)

    span: Span = ctx.get_item("inferred_proxy_span")
    assert span is not None
    assert span.name == span_name
    assert span.name in INFERRED_SPAN_NAMES
    assert span.span_type == "web"
    assert span.get_tag("span.kind") == "server"
    if not missing_resource_path:
        assert span.resource == "POST /{Path}"
    else:
        assert span.resource == "POST /http-api-path"
    assert span.service == "id.execute-api.us-east-1.amazonaws.com"
    assert span.start_ns == 1736973768000 * 1000000
    assert span.get_tag("component") == proxy_header
    assert span.get_tag("http.method") == "POST"
    assert span.get_tag("http.url") == "https://id.execute-api.us-east-1.amazonaws.com/http-api-path"
    if not missing_resource_path:
        assert span.get_tag("http.route") == "/{Path}"
    assert span.get_tag("stage") == "prod"
    assert span.get_tag("account_id") == "123456789012"
    assert span.get_tag("apiid") == "abcdef123456"
    assert span.get_tag("region") == "us-east-1"
    assert span.get_tag("aws_user") == "apigw-user"
    if proxy_header == "aws-httpapi":
        assert span.get_tag("dd_resource_key") == "arn:aws:apigateway:us-east-1::/apis/abcdef123456"
    elif proxy_header == "aws-apigateway":
        assert span.get_tag("dd_resource_key") == "arn:aws:apigateway:us-east-1::/restapis/abcdef123456"

    assert ctx.get_item("inferred_proxy_finish_callback") is not None


def test_create_inferred_proxy_span_for_azure_apim(tracer) -> None:
    ctx = ExecutionContext("test")
    headers = {
        "x-dd-proxy": "azure-apim",
        "x-dd-proxy-request-time-ms": "1736973768000",
        "x-dd-proxy-path": "/api/my-function",
        "x-dd-proxy-httpmethod": "GET",
        "x-dd-proxy-domain-name": "my-api.azure-api.net",
        "x-dd-proxy-resource-path": "/api/{resource}",
        "user-agent": "custom-client/1.0",
    }

    create_inferred_proxy_span_if_headers_exist(ctx, headers)

    span: Span = ctx.get_item("inferred_proxy_span")
    assert span is not None
    assert span.name == "azure.apim"
    assert span.name in INFERRED_SPAN_NAMES
    assert span.span_type == "web"
    assert span.get_tag("span.kind") == "server"
    assert span.resource == "GET /api/{resource}"
    assert span.service == "my-api.azure-api.net"
    assert span.start_ns == 1736973768000 * 1000000
    assert span.get_tag("component") == "azure-apim"
    assert span.get_tag("http.method") == "GET"
    assert span.get_tag("http.url") == "https://my-api.azure-api.net/api/my-function"
    assert span.get_tag("http.route") == "/api/{resource}"
    assert span.get_metric("_dd.inferred_span") == 1

    assert ctx.get_item("inferred_proxy_finish_callback") is not None


def test_create_inferred_proxy_span_for_azure_frontdoor(tracer) -> None:
    ctx = ExecutionContext("test")
    before = time.time_ns() // 1_000_000
    headers = {
        "x-dd-proxy": "azure-fd",
        # Front Door cannot set this header itself, so a value here is untrusted and must be ignored
        "x-dd-proxy-request-time-ms": "1736973768000",
        # path intentionally missing leading slash to test normalization
        "x-dd-proxy-path": "api/my-function",
        "x-dd-proxy-httpmethod": "GET",
        "x-dd-proxy-domain-name": "my-app.azurefd.net",
        "x-dd-proxy-resource-path": "/api/{resource}",
        "user-agent": "custom-client/1.0",
    }

    create_inferred_proxy_span_if_headers_exist(ctx, headers)
    after = time.time_ns() // 1_000_000

    span: Span = ctx.get_item("inferred_proxy_span")
    assert span is not None
    assert span.name == "azure.frontdoor"
    assert span.name in INFERRED_SPAN_NAMES
    assert span.span_type == "web"
    assert span.get_tag("span.kind") == "server"
    assert span.resource == "GET /api/{resource}"
    assert span.service == "my-app.azurefd.net"
    assert before * 1_000_000 <= span.start_ns <= after * 1_000_000
    assert span.get_tag("component") == "azure-fd"
    assert span.get_tag("http.method") == "GET"
    assert span.get_tag("http.url") == "https://my-app.azurefd.net/api/my-function"
    assert span.get_tag("http.route") == "/api/{resource}"
    assert span.get_metric("_dd.inferred_span") == 1

    assert ctx.get_item("inferred_proxy_finish_callback") is not None


def test_create_inferred_proxy_span_for_azure_frontdoor_without_timestamp(tracer) -> None:
    """Azure Front Door does not provide a timestamp header so the tracer falls back to the current time."""
    ctx = ExecutionContext("test")
    before = time.time_ns() // 1_000_000
    headers = {
        "x-dd-proxy": "azure-fd",
        "x-dd-proxy-path": "/api/my-function",
        "x-dd-proxy-httpmethod": "GET",
        "x-dd-proxy-domain-name": "my-app.azurefd.net",
    }

    create_inferred_proxy_span_if_headers_exist(ctx, headers)
    after = time.time_ns() // 1_000_000

    span: Span = ctx.get_item("inferred_proxy_span")
    assert span is not None
    assert span.name == "azure.frontdoor"
    # start_ns should be approximately now — between before and after (with ms precision)
    assert before * 1_000_000 <= span.start_ns <= after * 1_000_000

    assert ctx.get_item("inferred_proxy_finish_callback") is not None


def test_create_inferred_proxy_span_not_created_for_empty_timestamp_on_timestamp_provider(tracer) -> None:
    """Proxies that provide timestamps should not create a span if the timestamp header is present but empty."""
    ctx = ExecutionContext("test")
    headers = {
        "x-dd-proxy": "aws-apigateway",
        "x-dd-proxy-request-time-ms": "",
        "x-dd-proxy-path": "/test",
        "x-dd-proxy-httpmethod": "GET",
        "x-dd-proxy-domain-name": "example.com",
    }

    create_inferred_proxy_span_if_headers_exist(ctx, headers)

    assert ctx.get_item("inferred_proxy_span") is None


@pytest.mark.parametrize("bad_timestamp", ["not-a-number", "1736973768000.5", "1e12", " ", "0x64"])
def test_malformed_timestamp_does_not_leave_an_active_span(bad_timestamp, tracer) -> None:
    """A malformed timestamp must be rejected before a span is started, leaving the active span untouched."""
    ctx = ExecutionContext("test")
    headers = {
        "x-dd-proxy": "aws-apigateway",
        "x-dd-proxy-request-time-ms": bad_timestamp,
        "x-dd-proxy-path": "/test",
        "x-dd-proxy-httpmethod": "GET",
        "x-dd-proxy-domain-name": "example.com",
    }

    with tracer.trace("parent") as parent:
        create_inferred_proxy_span_if_headers_exist(ctx, headers)

        assert ctx.get_item("inferred_proxy_span") is None
        assert ctx.get_item("inferred_proxy_finish_callback") is None
        # no unfinished inferred span should have been activated
        assert tracer.current_span() is parent


def test_untrusted_timestamp_is_ignored_for_non_timestamp_provider(tracer) -> None:
    """A client-supplied timestamp must not reach start_ns for proxies that don't provide one."""
    ctx = ExecutionContext("test")
    headers = {
        "x-dd-proxy": "azure-fd",
        # a plausible-looking but arbitrary value that would otherwise distort the span duration
        "x-dd-proxy-request-time-ms": "1",
        "x-dd-proxy-path": "/api/my-function",
        "x-dd-proxy-httpmethod": "GET",
        "x-dd-proxy-domain-name": "my-app.azurefd.net",
    }

    before = time.time_ns() // 1_000_000
    create_inferred_proxy_span_if_headers_exist(ctx, headers)
    after = time.time_ns() // 1_000_000

    span: Span = ctx.get_item("inferred_proxy_span")
    assert span is not None
    assert before * 1_000_000 <= span.start_ns <= after * 1_000_000
