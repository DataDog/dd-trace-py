from wrapt import function_wrapper

from .helpers import func_name, get_inherited_pin, get_current_app


def wrap_function(instance, func, name=None, resource=None):
    """
    Helper function to wrap common flask.app.Flask methods.

    This helper will first ensure that a Pin is available and enabled before tracing
    """
    # TODO: Check to see if it is already wrapped
    #   Cannot do `if getattr(func, '__wrapped__', None)` because `functools.wraps` is used by third parties
    #   `isinstance(func, wrapt.ObjectProxy)` doesn't work because `tracer.wrap()` doesn't use `wrapt`
    if not name:
        name = func_name(func)

    @function_wrapper
    def trace_func(wrapped, _instance, args, kwargs):
        pin = get_inherited_pin(wrapped, _instance, instance, get_current_app())
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)
        with pin.tracer.trace(name, service=pin.service, resource=resource):
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
        pin = get_inherited_pin(wrapped, instance, app, get_current_app())
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        with pin.tracer.trace(name, service=pin.service) as span:
            span.set_tag('flask.signal', signal)
            return wrapped(*args, **kwargs)

    return trace_func(func)
