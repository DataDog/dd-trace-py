from typing import Optional

from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.internal import core
from ddtrace.internal.serverless import MICROVM_RUN_HOOK_METHOD
from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH
from ddtrace.internal.serverless import in_aws_lambda_microvm


def dispatch_web_request_starting(method: Optional[str], path: str) -> None:
    if method == MICROVM_RUN_HOOK_METHOD and path == MICROVM_RUN_HOOK_PATH and in_aws_lambda_microvm():
        core.dispatch(WebFrameworkEvents.WEB_REQUEST_STARTING.value, (method, path))
