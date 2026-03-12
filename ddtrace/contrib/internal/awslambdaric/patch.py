import awslambdaric
import functools

from ddtrace import __version__ as ddtrace_version
from ddtrace import tracer
from wrapt import wrap_function_wrapper as _w


is_cold_start = True


def wrap_request_handler(fn):

    @functools.wraps(fn)
    def _wrap(event, context):
        global is_cold_start

        function_arn = (context.invoked_function_arn or "").lower()
        tk = function_arn.split(":")
        function_arn = ":".join(tk[0:7]) if len(tk) > 7 else function_arn
        function_version = tk[7] if len(tk) > 7 else "$LATEST"

        with tracer.trace("aws.lambda", service="aws.lambda") as span:
            tags = {
                "cold_start": str(is_cold_start).lower(),
                "function_arn": function_arn,
                "function_version": function_version,
                "request_id": context.aws_request_id,
                "resource_names": context.function_name,
                "functionname": (
                    context.function_name.lower() if context.function_name else None
                ),
                "datadog_lambda": ddtrace_version,
                "dd_trace": ddtrace_version,
                "span.name": "aws.lambda",
            }
            span.set_tags(tags)

            try:
                return fn(event, context)
            finally:
                is_cold_start = False

    return _wrap


def wrap_handle_event_request(wrapped, instance, args, kwargs):
    request_handler = args[1]
    request_handler = wrap_request_handler(request_handler)
    wrapped_args = (args[0], request_handler) + args[2:]
    return wrapped(*wrapped_args)


def patch():
    if not getattr(awslambdaric, "_datadog_patch", False):
        try:
            _w("awslambdaric.bootstrap", "handle_event_request", wrap_handle_event_request)
        finally:
            setattr(awslambdaric, "_datadog_patch", True)


def unpatch():
    pass
