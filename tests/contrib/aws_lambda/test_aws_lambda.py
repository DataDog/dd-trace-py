import os

import pytest

from ddtrace.contrib.aws_lambda import patch
from ddtrace.contrib.aws_lambda import unpatch
from tests.contrib.aws_lambda.handlers import datadog
from tests.contrib.aws_lambda.handlers import finishing_spans_early_handler
from tests.contrib.aws_lambda.handlers import handler
from tests.contrib.aws_lambda.handlers import manually_wrapped_handler
from tests.contrib.aws_lambda.handlers import timeout_handler


class LambdaContext:
    def __init__(self):
        self.invoked_function_arn = "arn:aws:lambda:us-east-1:000000000000:function:fake-function-name"
        self.memory_limit_in_mb = 2048
        self.client_context = {}
        self.aws_request_id = "request-id-1"
        self.function_version = "1"

    def get_remaining_time_in_millis(self):
        return 2000


@pytest.fixture()
def context():
    return LambdaContext()


@pytest.fixture(autouse=True)
def setup():
    os.environ.update(
        {
            "DD_TRACE_AGENT_URL": "http://localhost:9126/",
            "DD_TRACE_ENABLED": "true",
        }
    )
    yield
    unpatch()


@pytest.mark.snapshot()
def test_timeout_traces(context):
    os.environ.update(
        {
            "AWS_LAMBDA_FUNCTION_NAME": "timeout_handler",
            "DD_LAMBDA_HANDLER": "tests.contrib.aws_lambda.handlers.timeout_handler",
        }
    )

    patch()

    with pytest.raises(Exception) as e:
        datadog(timeout_handler)({}, context)
        assert e.type is Exception


@pytest.mark.snapshot()
def test_continue_on_early_trace_ending(context):
    """
    These scenario expects no timeout error being tagged on the root span
    when closing all spans in the customers code and reaching a timeout.
    """
    os.environ.update(
        {
            "AWS_LAMBDA_FUNCTION_NAME": "finishing_spans_early_handler",
            "DD_LAMBDA_HANDLER": "tests.contrib.aws_lambda.handlers.finishing_spans_early_handler",
        }
    )

    patch()

    datadog(finishing_spans_early_handler)({}, context)


@pytest.mark.snapshot
async def test_file_patching(context):
    os.environ.update(
        {
            "AWS_LAMBDA_FUNCTION_NAME": "handler",
            "DD_LAMBDA_HANDLER": "tests.contrib.aws_lambda.handlers.handler",
        }
    )

    patch()

    result = datadog(handler)({}, context)

    assert result == {"success": True}
    return


@pytest.mark.snapshot
async def test_module_patching(mocker, context):
    mocker.patch("datadog_lambda.wrapper._LambdaDecorator._before")
    mocker.patch("datadog_lambda.wrapper._LambdaDecorator._after")

    os.environ.update(
        {
            "AWS_LAMBDA_FUNCTION_NAME": "manually_wrapped_handler",
        }
    )

    os.environ.pop("DD_LAMBDA_HANDLER")
    patch()

    result = manually_wrapped_handler({}, context)

    assert result == {"success": True}
    return
