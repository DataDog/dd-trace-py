from typing import Optional

from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.internal import core
from ddtrace.internal.serverless import MICROVM_RUN_HOOK_METHOD
from ddtrace.internal.serverless import MICROVM_RUN_HOOK_PATH
from ddtrace.internal.serverless import in_aws_lambda_microvm


_WEB_REQUEST_STARTING_DISPATCHED = "_ddtrace_web_request_starting_dispatched"


def dispatch_web_request_starting(method: Optional[str], path_prefix: str, path: str) -> bool:
    if method != MICROVM_RUN_HOOK_METHOD or not in_aws_lambda_microvm():
        return False

    path = path_prefix.rstrip("/") + path
    if path != MICROVM_RUN_HOOK_PATH:
        return False

    core.dispatch(WebFrameworkEvents.WEB_REQUEST_STARTING.value, (method, path))
    return True
