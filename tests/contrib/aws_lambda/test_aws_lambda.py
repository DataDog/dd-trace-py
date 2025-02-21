import pytest

from ddtrace.contrib.internal.aws_lambda.patch import patch
from ddtrace.contrib.internal.aws_lambda.patch import unpatch
from tests.contrib.aws_lambda.handlers import class_handler
from tests.contrib.aws_lambda.handlers import datadog
from tests.contrib.aws_lambda.handlers import finishing_spans_early_handler
from tests.contrib.aws_lambda.handlers import handler
from tests.contrib.aws_lambda.handlers import instance_handler
from tests.contrib.aws_lambda.handlers import instance_handler_with_code
from tests.contrib.aws_lambda.handlers import manually_wrapped_handler
from tests.contrib.aws_lambda.handlers import static_handler
from tests.contrib.aws_lambda.handlers import timeout_handler
from tests.utils import flaky
from tests.utils import override_env


class LambdaContext:
    def __init__(self, remaining_time_in_millis=300):
        self.invoked_function_arn = "arn:aws:lambda:us-east-1:000000000000:function:fake-function-name"
        self.memory_limit_in_mb = 2048
        self.client_context = {}
        self.aws_request_id = "request-id-1"
        self.function_version = "1"
        self.remaining_time_in_millis = remaining_time_in_millis

    def get_remaining_time_in_millis(self):
        return self.remaining_time_in_millis


@pytest.fixture()
def context():
    def create_context(remaining_time_in_millis=300):
        return LambdaContext(remaining_time_in_millis)

    return create_context


def get_env(env=None):
    if env is None:
        env = {}
    common_env = {
        "DD_TRACE_AGENT_URL": "http://localhost:9126/",
        "DD_TRACE_ENABLED": "true",
    }
    return {**common_env, **env}


@pytest.fixture(autouse=True)
def setup():
    yield
    unpatch()


@flaky(1735812000)
@pytest.mark.parametrize("customApmFlushDeadline", [("-100"), ("10"), ("100"), ("200")])
@pytest.mark.snapshot
def test_timeout_traces(context, customApmFlushDeadline):
    env = get_env(
        {
            "AWS_LAMBDA_FUNCTION_NAME": "timeout_handler",
            "DD_LAMBDA_HANDLER": "tests.contrib.aws_lambda.handlers.timeout_handler",
            "DD_APM_FLUSH_DEADLINE_MILLISECONDS": customApmFlushDeadline,
        }
    )

    with override_env(env):
        patch()

        datadog(timeout_handler)({}, context())


@pytest.mark.snapshot
def test_continue_on_early_trace_ending(context):
    """
    These scenario expects no timeout error being tagged on the root span
    when closing all spans in the customers code and reaching a timeout.
    """
    env = get_env(
        {
            "AWS_LAMBDA_FUNCTION_NAME": "finishing_spans_early_handler",
            "DD_LAMBDA_HANDLER": "tests.contrib.aws_lambda.handlers.finishing_spans_early_handler",
        }
    )

    with override_env(env):
        patch()

        datadog(finishing_spans_early_handler)({}, context())


@pytest.mark.snapshot
async def test_file_patching(context):
    env = get_env(
        {
            "AWS_LAMBDA_FUNCTION_NAME": "handler",
            "DD_LAMBDA_HANDLER": "tests.contrib.aws_lambda.handlers.handler",
        }
    )

    with override_env(env):
        patch()

        result = datadog(handler)({}, context())

        assert result == {"success": True}


@pytest.mark.snapshot
async def test_module_patching(mocker, context):
    mocker.patch("datadog_lambda.wrapper._LambdaDecorator._before")
    mocker.patch("datadog_lambda.wrapper._LambdaDecorator._after")

    env = get_env(
        {
            "AWS_LAMBDA_FUNCTION_NAME": "manually_wrapped_handler",
        }
    )

    with override_env(env):
        patch()

        result = manually_wrapped_handler({}, context())

        assert result == {"success": True}


@pytest.mark.parametrize(
    "handler,function_name",
    [
        (static_handler, "static_handler"),
        (class_handler, "class_handler"),
        (instance_handler, "instance_handler"),
        (instance_handler_with_code, "instance_handler_with_code"),
    ],
)
@pytest.mark.snapshot
@flaky(1741838400, reason="Did not receive expected traces: 'aws.lambda' for [handler3-instance_handler_with_code]")
def test_class_based_handlers(context, handler, function_name):
    env = get_env(
        {
            "AWS_LAMBDA_FUNCTION_NAME": function_name,
            "DD_LAMBDA_HANDLER": "tests.contrib.aws_lambda.handlers." + function_name,
        }
    )

    with override_env(env):
        patch()

        result = datadog(handler)({}, context())
        assert result == {"success": True}
