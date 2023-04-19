from ddtrace import config
from ddtrace.appsec.iast._util import _is_iast_enabled
from ddtrace.internal.constants import COMPONENT
from ddtrace.vendor.wrapt import function_wrapper

from .. import trace_utils
from ...internal.utils.importlib import func_name
from ...pin import Pin
from .helpers import get_current_app


def wrap_function(instance, func, name=None, resource=None):
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

            # If IAST is enabled, taint the Flask function kwargs (path parameters)
            if _is_iast_enabled() and kwargs:
                from ddtrace.appsec.iast._input_info import Input_info
                from ddtrace.appsec.iast._taint_tracking import taint_pyobject

                for k, v in kwargs.items():
                    kwargs[k] = taint_pyobject(v, Input_info(k, v, "http.request.path.parameter"))

            return wrapped(*args, **kwargs)

    return trace_func(func)


def wrap_signal(app, signal, func):
    """
    Helper used to wrap signal handlers

    We will attempt to find the pin attached to the flask.app.Flask app
    """
    name = func_name(func)

    @function_wrapper
    def trace_func(wrapped, instance, args, kwargs):
        pin = Pin._find(wrapped, instance, app, get_current_app())
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        with pin.tracer.trace(name, service=trace_utils.int_service(pin, config.flask)) as span:
            span.set_tag_str(COMPONENT, config.flask.integration_name)

            span.set_tag_str("flask.signal", signal)
            return wrapped(*args, **kwargs)

    return trace_func(func)
