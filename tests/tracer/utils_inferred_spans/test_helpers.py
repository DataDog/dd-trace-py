from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE


def assert_aws_api_gateway_span_behavior(aws_gateway_span, service_name=None):
    """
    Used to assert that the resulting inferred span meets the expected common attributes
    """
    assert aws_gateway_span.name == "aws.apigateway"
    assert aws_gateway_span.service == service_name
    assert aws_gateway_span.get_tag("span.kind") == "internal"
    assert aws_gateway_span.get_tag("component") == "aws-apigateway"
    assert aws_gateway_span.get_tag("_dd.inferred_span") == "1"
    assert aws_gateway_span.span_type == "web"


def assert_web_and_inferred_aws_api_gateway_common_metadata(web_span, aws_gateway_span):
    """
    Used to assert that the resulting inferred span has the matching span metadata as the web span
    """
    assert web_span is not None
    assert aws_gateway_span is not None

    assert aws_gateway_span.span_id == web_span.parent_id
    assert aws_gateway_span.trace_id == web_span.trace_id

    # Assert the span types match
    assert aws_gateway_span.span_type == web_span.span_type

    # Assert that the http method, status code, and route match
    assert aws_gateway_span.get_tag("http.method") == web_span.get_tag("http.method")
    assert aws_gateway_span.get_tag("http.status_code") == web_span.get_tag("http.status_code")

    # Assert errors (or lack of them) are correctly passed on
    assert aws_gateway_span.error == web_span.error
    assert web_span.get_tag(ERROR_MSG) == aws_gateway_span.get_tag(ERROR_MSG)
    assert web_span.get_tag(ERROR_TYPE) == aws_gateway_span.get_tag(ERROR_TYPE)
    assert web_span.get_tag(ERROR_STACK) == aws_gateway_span.get_tag(ERROR_STACK)
