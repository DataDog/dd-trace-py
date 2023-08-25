from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.vendor.wrapt import function_wrapper

from .. import trace_utils
from ...internal.logger import get_logger
from ...internal.utils.importlib import func_name
from ...pin import Pin
from .helpers import get_current_app


log = get_logger(__name__)


def wrap_view(instance, func, name=None, resource=None):
    """
    Helper function to wrap common flask.app.Flask methods.

    This helper will first ensure that a Pin is available and enabled before tracing
    """
    if not name:
        name = func_name(func)

    @function_wrapper
    def trace_func(wrapped, _instance, args, kwargs):
        pin = Pin._find(wrapped, _instance, instance, get_current_app())
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)
        with pin.tracer.trace(name, service=trace_utils.int_service(pin, config.flask), resource=resource) as span:
            span.set_tag_str(COMPONENT, config.flask.integration_name)

            results, exceptions = core.dispatch("flask.wrapped_view", [kwargs])
            if results and results[0]:
                callback_block, _kwargs = results[0]
                if callback_block:
                    return callback_block()
                if _kwargs:
                    for k in kwargs:
                        kwargs[k] = _kwargs[k]

            return wrapped(*args, **kwargs)

    return trace_func(func)


def _wrap_call(func, instance, name, resource):
    @function_wrapper
    def trace_func(wrapped, _instance, args, kwargs):
        pin = Pin._find(wrapped, _instance, instance, get_current_app())
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)
        with core.context_with_data(
            "flask.function", name=name, pin=pin, flask_config=config.flask, resource=resource
        ) as ctx, ctx.get_item("flask_call"):
            return wrapped(*args, **kwargs)

    return trace_func(func)


def wrap_function(instance, func, name=None, resource=None):
    return _wrap_call(func, instance, name or func_name(func), resource)


def wrap_signal(app, signal, func):
    """
    Helper used to wrap signal handlers

    We will attempt to find the pin attached to the flask.app.Flask app
    """
    name = func_name(func)

    @function_wrapper
    def patch_func(wrapped, instance, args, kwargs):
        pin = Pin._find(wrapped, instance, app, get_current_app())
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        with core.context_with_data(
            "flask.signal", name=name, signal=signal, pin=pin, flask_config=config.flask
        ) as ctx, ctx.get_item("flask_call"):
            return wrapped(*args, **kwargs)

    return patch_func(func)
