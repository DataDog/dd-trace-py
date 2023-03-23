import time

from datadog_lambda.wrapper import datadog_lambda_wrapper

from ddtrace import tracer


@datadog_lambda_wrapper
def manually_wrapped_handler(event, context):
    with tracer.trace("result-trace"):
        return {"success": True}


def handler(event, context):
    return {"success": True}


def timeout_handler(event, context):
    @tracer.wrap("timeout")
    def timeout():
        time.sleep(3)

    timeout()

    return {"success": False}


def finishing_spans_early_handler(event, context):
    root_span = tracer.current_root_span()
    root_span.finish()

    time.sleep(0.5)

    return {"success": False}


class CallableHandler:
    """Mocks a callable handler. Used in frameworks like Chalice."""

    def __call__(self, event, context):
        with tracer.trace("result-trace"):
            return {"success": True}


callable_handler = CallableHandler()
manually_wrapped_callable_handler = datadog_lambda_wrapper(callable_handler)


def datadog(_handler):
    @tracer.wrap("aws.lambda")
    def wrapped(*args):
        return _handler(*args)

    return wrapped
