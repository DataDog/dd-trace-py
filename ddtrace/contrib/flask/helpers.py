import flask

from ddtrace import Pin
from ddtrace import config

from .. import trace_utils


def get_current_app():
    """Helper to get the flask.app.Flask from the current app context"""
    try:
        return flask.current_app
    except RuntimeError:
        # raised if current_app is None: https://github.com/pallets/flask/blob/2.1.3/src/flask/globals.py#L40
        pass
    return None


def with_instance_pin(func):
    """Helper to wrap a function wrapper and ensure an enabled pin is available for the `instance`"""

    def wrapper(wrapped, instance, args, kwargs):
        pin = Pin._find(wrapped, instance, get_current_app())
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        return func(pin, wrapped, instance, args, kwargs)

    return wrapper


def simple_tracer(name, span_type=None):
    """Generate a simple tracer that wraps the function call with `with tracer.trace()`"""

    @with_instance_pin
    def wrapper(pin, wrapped, instance, args, kwargs):
        with pin.tracer.trace(name, service=trace_utils.int_service(pin, config.flask, pin), span_type=span_type):
            return wrapped(*args, **kwargs)

    return wrapper
