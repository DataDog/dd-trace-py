import functools

from ddtrace.vendor import six

from ddtrace import Pin
from ddtrace.compat import contextvars

from .. import trace_utils


@trace_utils.with_traced_module
def deferred_init(cyclone, pin, func, instance, args, kwargs):
    # Create a new context for this Deferred
    ctx = contextvars.copy_context()
    instance.__ctx = ctx
    return func(*args, **kwargs)


@trace_utils.with_traced_module
def deferred_callback(cyclone, pin, func, instance, args, kwargs):
    callback = args[0] or kwargs.pop("callback")
    ctx = instance.__ctx

    @functools.wraps(callback)
    def _callback(*args, **kwargs):
        # TODO: contextvars throws a RuntimeError if the context is already
        #  active, however it doesn't provide a way to check whether this is
        #  the case or not. This duck typing of the exception is not ideal
        #  but it will have to do until we can think of something better.
        try:
            return ctx.run(callback, *args, **kwargs)
        except RuntimeError as e:
            if "cannot enter context" in str(e):
                return callback(*args, **kwargs)
            six.reraise(e)

    newargs = list(args)
    newargs[0] = _callback
    return func(*tuple(newargs), **kwargs)


def patch():
    import twisted

    if getattr(twisted, "__datadog_patch", False):
        return

    Pin().onto(twisted)

    trace_utils.wrap("twisted.internet.defer", "Deferred.__init__", deferred_init(twisted))
    trace_utils.wrap("twisted.internet.defer", "Deferred.addCallback", deferred_callback(twisted))
    trace_utils.wrap("twisted.internet.defer", "Deferred.addCallbacks", deferred_callback(twisted))


def unpatch():
    import twisted

    if not getattr(twisted, "__datadog_patch", False):
        return

    trace_utils.unwrap(twisted.internet.defer.Deferred, "__init__")
    trace_utils.unwrap(twisted.internet.defer.Deferred, "addCallback")
    trace_utils.unwrap(twisted.internet.defer.Deferred, "addCallbacks")

    setattr(twisted, "__datadog_patch", False)
