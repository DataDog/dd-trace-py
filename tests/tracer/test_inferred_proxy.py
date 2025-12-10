import pytest

from ddtrace._trace._inferred_proxy import SUPPORTED_PROXY_SPAN_NAMES
from ddtrace._trace._inferred_proxy import create_inferred_proxy_span_if_headers_exist
from ddtrace.internal.core import ExecutionContext
from tests.utils import DummyTracer


@pytest.mark.parametrize(
    "proxy_header,span_name", [("aws-httpapi", "aws.httpapi"), ("aws-apigateway", "aws.apigateway")]
)
def test_create_inferred_proxy_span_for_apigateway(proxy_header, span_name):
    tracer = DummyTracer()
    ctx = ExecutionContext("test")
    headers = {
        "x-dd-proxy": proxy_header,
        "x-dd-proxy-request-time-ms": "1736973768000",
        "x-dd-proxy-path": "/http-api-path",
        "x-dd-proxy-resource-path": "/{Path}",
        "x-dd-proxy-httpmethod": "POST",
        "x-dd-proxy-domain-name": "id.execute-api.us-east-1.amazonaws.com",
        "x-dd-proxy-stage": "prod",
        "x-dd-proxy-account-id": "123456789012",
        "x-dd-proxy-api-id": "abcdef123456",
        "x-dd-proxy-region": "us-east-1",
        "x-dd-proxy-user": "apigw-user",
    }

    create_inferred_proxy_span_if_headers_exist(ctx, headers, child_of=None, tracer=tracer)

    span = ctx.get_item("inferred_proxy_span")
    assert span is not None
    assert span.name == span_name
    assert span.name in SUPPORTED_PROXY_SPAN_NAMES
    assert span.resource == "POST /{Path}"
    assert span.service == "id.execute-api.us-east-1.amazonaws.com"
    assert span.start_ns == 1736973768000 * 1000000
    assert span.get_tag("component") == proxy_header
    assert span.get_tag("http.method") == "POST"
    assert span.get_tag("http.url") == "https://id.execute-api.us-east-1.amazonaws.com/http-api-path"
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
