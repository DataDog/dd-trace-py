import functools
import sys

from ddtrace.vendor import six

from ddtrace import Pin
from ddtrace.compat import contextvars

from .. import trace_utils


def deferred_init(func, instance, args, kwargs):
    # Create a new context for this Deferred
    ctx = contextvars.copy_context()
    instance.__ctx = ctx
    return func(*args, **kwargs)


def deferred_callback(func, instance, args, kwargs):
    callback = args[0] or kwargs.pop("callback")

    @functools.wraps(callback)
    def _callback(*args, **kwargs):

        # ctx.run could raise a RuntimeError if the context is already
        # activated. This should not happen in practice even if there
        # is a recursive callback since the wrapper will not be called
        # with the recursion call.
        # eg.
        # Consider the callback function
        # def callback(n):
        #     return callback(n-1) if n > 1 else 0
        #
        # this function will be intercepted and replaced with a wrapped
        # version when addCallbacks is called.
        # When the function is invoked the recursive callback(n-1) call
        # will not call the wrapping code again.
        #
        #
        ctx = instance.__ctx
        try:
            return ctx.run(callback, *args, **kwargs)
        except RuntimeError as e:
            if "cannot enter context" in str(e):
                return callback(*args, **kwargs)
            exc_type, exc_val, exc_tb = sys.exc_info()
            six.reraise(exc_type, exc_val, exc_tb)

    newargs = list(args)
    newargs[0] = _callback
    return func(*tuple(newargs), **kwargs)


def patch():
    import twisted

    if getattr(twisted, "__datadog_patch", False):
        return

    Pin().onto(twisted)

    trace_utils.wrap("twisted.internet.defer", "Deferred.__init__", deferred_init)
    trace_utils.wrap("twisted.internet.defer", "Deferred.addCallbacks", deferred_callback)


def unpatch():
    import twisted

    if not getattr(twisted, "__datadog_patch", False):
        return

    trace_utils.unwrap(twisted.internet.defer.Deferred, "__init__")
    trace_utils.unwrap(twisted.internet.defer.Deferred, "addCallbacks")

    setattr(twisted, "__datadog_patch", False)
