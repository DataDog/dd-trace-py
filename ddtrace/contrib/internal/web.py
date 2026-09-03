from typing import Optional

from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.internal import core
from ddtrace.internal.serverless import in_aws_lambda_microvm


_LAMBDA_MICROVM_RUN_PATH = "/aws/lambda-microvms/runtime/v1/run"


def dispatch_web_request_starting(method: Optional[str], path: str) -> None:
    if method == "POST" and path == _LAMBDA_MICROVM_RUN_PATH and in_aws_lambda_microvm():
        core.dispatch(WebFrameworkEvents.WEB_REQUEST_STARTING.value, (method, path))
