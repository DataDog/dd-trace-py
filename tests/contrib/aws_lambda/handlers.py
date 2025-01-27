import time

from datadog_lambda.wrapper import datadog_lambda_wrapper

from ddtrace.trace import tracer


@datadog_lambda_wrapper
def manually_wrapped_handler(event, context, success=True):
    with tracer.trace("result-trace"):
        return {"success": success}


def handler(event, context, success=True):
    return {"success": success}


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


class StaticMeta(type):
    @staticmethod
    def __call__(event, context):
        with tracer.trace("result-trace"):
            return {"success": True}


class ClassMeta(type):
    @classmethod
    def __call__(cls, event, context):
        with tracer.trace("result-trace"):
            return {"success": True}


class ClassHandler(metaclass=ClassMeta):
    pass


class StaticHandler(metaclass=StaticMeta):
    pass


class InstanceHandler:
    def __call__(self, event, context):
        with tracer.trace("result-trace"):
            return {"success": True}


class InstanceHandlerWithCode(InstanceHandler):
    def __code__(self):
        return


static_handler = StaticHandler
class_handler = ClassHandler
instance_handler = InstanceHandler()
instance_handler_with_code = InstanceHandlerWithCode()


def datadog(_handler):
    @tracer.wrap("aws.lambda")
    def wrapped(*args):
        return _handler(*args)

    return wrapped
