import time

from datadog_lambda.wrapper import datadog_lambda_wrapper

from ddtrace import tracer


@datadog_lambda_wrapper
def manually_wrapped_handler(event, context):
    @tracer.wrap("result-trace")
    def get_result():
        return {"success": True}

    return get_result()


def handler(event, context):
    return {"success": True}


def timeout_handler(event, context):
    @tracer.wrap("timeout")
    def timeout():
        time.sleep(3)

    timeout()

    return {"success": False}


def datadog(_handler):
    @tracer.wrap("aws.lambda")
    def wrapped(*args):
        return _handler(*args)

    return wrapped
