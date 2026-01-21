from wrapt import wrap_function_wrapper as _w

from ddtrace.trace import tracer

def _context_method_wrapper(wrapped, instance, args, kwargs):
    def _get_name(*ar, name=None, **kw):
        return name

    name = _get_name(*args, **kwargs) or wrapped.__name__
    with tracer.trace(
        name,
        service="aws.lambda.durable",
        resource=wrapped.__name__,
        span_type="serverless",
    ):
        return wrapped(*args, **kwargs)

_context_methods_to_wrap = (
    "create_callback",
    "invoke",
    "map",
    "parallel",
    "run_in_child_context",
    "step",
    "wait",
    "wait_for_callback",
    "wait_for_condition",
)

def patch():
    print("PATCHING AWS DURABLE FUNCTION!")
    for method in _context_methods_to_wrap:
        _w("aws_durable_execution_sdk_python.context", f"DurableContext.{method}", _context_method_wrapper)
