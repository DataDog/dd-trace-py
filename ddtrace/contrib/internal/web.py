from typing import Optional

from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.internal import core
from ddtrace.internal.serverless import in_aws_lambda_microvm


_LAMBDA_MICROVM_RUN_PATH = "/aws/lambda-microvms/runtime/v1/run"
_WEB_REQUEST_STARTING_DISPATCHED = "_ddtrace_web_request_starting_dispatched"


def dispatch_web_request_starting(method: Optional[str], path_prefix: str, path: str) -> bool:
    if method != "POST" or not in_aws_lambda_microvm():
        return False

    path = path_prefix.rstrip("/") + path
    if path != _LAMBDA_MICROVM_RUN_PATH:
        return False

    core.dispatch(WebFrameworkEvents.WEB_REQUEST_STARTING.value, (method, path))
    return True
