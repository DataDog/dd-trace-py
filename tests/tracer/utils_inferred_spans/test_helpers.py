from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE


def assert_web_and_inferred_aws_api_gateway_span_data(
    aws_gateway_span,
    web_span,
    web_span_name,
    web_span_component,
    web_span_service_name,
    web_span_resource,
    api_gateway_service_name,
    api_gateway_resource,
    method,
    status_code,
    url,
    start,
    is_distributed=False,
    distributed_trace_id=None,
    distributed_parent_id=None,
    distributed_sampling_priority=None,
):
    """
    Used to assert that the resulting inferred span meets the expected common attributes
    """
    assert web_span is not None
    assert aws_gateway_span is not None

    assert aws_gateway_span.span_id == web_span.parent_id
    assert aws_gateway_span.trace_id == web_span.trace_id

    # assert some web span attributes
    assert web_span.name == web_span_name
    assert web_span.service == web_span_service_name
    assert web_span.resource == web_span_resource
    assert web_span.get_tag("span.kind") == "server"
    assert web_span.get_tag("component") == web_span_component
    assert web_span.get_metric("_dd.inferred_span") is None

    # assert top level api gateway attributes
    assert aws_gateway_span.name == "aws.apigateway"
    assert aws_gateway_span.service == api_gateway_service_name
    assert aws_gateway_span.resource == api_gateway_resource
    assert aws_gateway_span.span_type == "web"
    assert aws_gateway_span.start == start

    # assert basic api gateway meta
    assert aws_gateway_span.get_tag("component") == "aws-apigateway"
    assert aws_gateway_span.get_metric("_dd.inferred_span") == 1

    # assert api gateway http meta
    assert aws_gateway_span.get_tag("http.method") == method
    assert int(aws_gateway_span.get_tag("http.status_code")) == int(status_code)
    assert aws_gateway_span.get_tag("http.url") == url

    # Assert that the http method and status code match
    assert aws_gateway_span.get_tag("http.method") == web_span.get_tag("http.method")
    assert aws_gateway_span.get_tag("http.status_code") == web_span.get_tag("http.status_code")

    # Assert errors (or lack of them) are correctly passed on
    assert aws_gateway_span.error == web_span.error
    assert web_span.get_tag(ERROR_MSG) == aws_gateway_span.get_tag(ERROR_MSG)
    assert web_span.get_tag(ERROR_TYPE) == aws_gateway_span.get_tag(ERROR_TYPE)
    assert web_span.get_tag(ERROR_STACK) == aws_gateway_span.get_tag(ERROR_STACK)

    if is_distributed:
        assert aws_gateway_span.trace_id == distributed_trace_id
        assert aws_gateway_span.parent_id == distributed_parent_id
        assert aws_gateway_span.get_metric(_SAMPLING_PRIORITY_KEY) == distributed_sampling_priority
