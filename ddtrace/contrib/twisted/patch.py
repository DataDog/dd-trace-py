import inspect
import functools
import sys

from ddtrace.vendor import six

from ddtrace import Pin, config
from ddtrace.compat import contextvars
from ddtrace.contrib import func_name
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.ext import errors
from ddtrace.utils.formats import asbool, get_env
from ddtrace.propagation.http import HTTPPropagator

from .. import trace_utils


config._add(
    "twisted",
    dict(
        _default_service="twisted",
        distributed_tracing=asbool(get_env("twisted", "distributed_tracing", default=True)),
        split_by_domain=asbool(get_env("twisted", "split_by_domain", default=False)),
        trace_all_deferreds=asbool(get_env("twisted", "trace_all_deferreds", default=False)),
    ),
)


@trace_utils.with_traced_module
def deferred_init(twisted, pin, func, instance, args, kwargs):
    # Create a new context for this Deferred
    ctx = contextvars.copy_context()
    ddctx = pin.tracer.get_call_context()
    instance.__ctx = ctx
    instance.__ddctx = ddctx

    # Only create traces for deferreds when there is an active span
    # or there is a name in the context.
    if ddctx.get_current_span() and ddctx.get_ctx_item("trace_deferreds", default=config.twisted.trace_all_deferreds):
        resource = ddctx.get_ctx_item("deferred_resource", None)

        if not resource:
            # Go up two frames to get to the function that created the deferred
            # in order to extract the class name (if any) and method/    function.
            # <fn we care about that creates the deferred>
            # wrapper (from with_traced_module)
            # Deferred.__init__
            # This wrapper (currentframe())
            stack = inspect.stack()
            f_locals = stack[2][0].f_locals
            method_name = stack[2][0].f_code.co_name
            if "self" in f_locals:
                cls = f_locals["self"].__class__.__name__
                resource = "%s.%s" % (cls, method_name)
            else:
                resource = "%s" % method_name

        span = pin.tracer.trace("deferred", resource=resource, service=trace_utils.int_service(pin, config.twisted))
        instance.__ddspan = span

    return func(*args, **kwargs)


@trace_utils.with_traced_module
def deferred_callback(twisted, pin, func, instance, args, kwargs):
    span = getattr(instance, "__ddspan", None)
    if span and not span.finished:
        for n, cb in enumerate(instance.callbacks):
            # cb is a tuple of
            # (
            #   (callback, callbackArgs, callbackKWArgs),
            #   (errback, errbackArgs, errbackKWArgs)
            # )
            span.set_tag("callback.%d" % n, func_name(cb[0][0]))
            span.set_tag("errback.%d" % n, func_name(cb[1][0]))
        span.finish()

    return func(*args, **kwargs)


@trace_utils.with_traced_module
def deferred_errback(twisted, pin, func, instance, args, kwargs):
    span = getattr(instance, "__ddspan", None)
    if span and not span.finished:
        if len(args) > 0:
            f = args[0]
            if hasattr(f, "getTraceback"):
                tb = f.getTraceback()
                span.set_tag(errors.ERROR_STACK, tb)
            if hasattr(f, "getErrorMessage"):
                span.set_tag(errors.ERROR_MSG, f.getErrorMessage())
            if hasattr(f, "type"):
                span.set_tag(errors.ERROR_TYPE, f.type)
        span.error = 1
        span.finish()

    return func(*args, **kwargs)


@trace_utils.with_traced_module
def deferred_addCallbacks(twisted, pin, func, instance, args, kwargs):
    callback = args[0] or kwargs.pop("callback")

    @functools.wraps(callback)
    def _callback(*args, **kwargs):
        ctx = instance.__ctx
        ddctx = instance.__ddctx

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
        # Regardless, let's safe-guard it as to not interfere with the
        # original code.
        try:
            # Need to wrap the callback again to copy the datadog context
            # once the contextvars context is activated.
            def fn(*args, **kwargs):
                pin.tracer.context_provider.activate(ddctx.clone())
                return callback(*args, **kwargs)

            return ctx.run(fn, *args, **kwargs)
        except RuntimeError as e:
            if "cannot enter context" in str(e):
                return callback(*args, **kwargs)
            exc_type, exc_val, exc_tb = sys.exc_info()
            six.reraise(exc_type, exc_val, exc_tb)

    newargs = list(args)
    newargs[0] = _callback
    return func(*tuple(newargs), **kwargs)


@trace_utils.with_traced_module
def threadpool_callInThreadWithCallback(twisted, pin, func, instance, args, kwargs):
    f = args[1]

    ctx = pin.tracer.get_call_context().clone()

    # Due to an oversight in Context, whenever a span is closed
    # the parent is set as the active. However in async tracing the child
    # can outlive the parent...

    # In this case the handler method will probably close, setting the parent
    # to the request span. However there may be child spans still open
    # (like runQuery).
    # To get around this we have to clone the context and handle them
    # separately.

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        prev_ctx = pin.tracer.get_call_context()
        try:
            pin.tracer.context_provider.activate(ctx)
            return f(*args, **kwargs)
        finally:
            pin.tracer.context_provider.activate(prev_ctx)

    newargs = list(args)
    newargs[1] = wrapper
    return func(*tuple(newargs), **kwargs)


@trace_utils.with_traced_module
def connectionpool___init__(twisted, pin, func, instance, args, kwargs):
    try:
        return func(*args, **kwargs)
    finally:
        f = instance.connectionFactory

        def factory(*args, **kwargs):
            return TracedConnection(f(*args, **kwargs), pin)

        instance.connectionFactory = factory


@trace_utils.with_traced_module
def connectionpool_runquery(twisted, pin, func, instance, args, kwargs):
    ctx = pin.tracer.get_call_context()
    with ctx.override_ctx_item("trace_deferreds", True):
        with ctx.override_ctx_item("deferred_resource", "ConnectionPool.runQuery"):
            return func(*args, **kwargs)


@trace_utils.with_traced_module
def httpclientfactory___init__(twisted, pin, func, instance, args, kwargs):
    ctx = pin.tracer.get_call_context()
    try:
        with ctx.override_ctx_item("trace_deferreds", True):
            return func(*args, **kwargs)
    finally:
        # Note that HTTPClientFactory creates a Deferred in its init
        # so we want that to be the active span for when we set the distributed
        # tracing headers.
        if config.twisted.distributed_tracing:
            propagator = HTTPPropagator()
            propagator.inject(ctx, instance.headers)


@trace_utils.with_traced_module
def reactor_callLater(twisted, pin, func, instance, args, kwargs):
    ctx = pin.tracer.get_call_context().clone()

    delay = args[0]
    fn = args[1]

    def traced_fn(*args, **kwargs):
        try:
            prev_ctx = pin.tracer.get_call_context()
            pin.tracer.context_provider.activate(ctx)
            return fn(*args, **kwargs)
        finally:
            pin.tracer.context_provider.activate(prev_ctx)

    newargs = list(args)
    newargs[1] = traced_fn
    return func(*tuple(newargs), **kwargs)



def patch():
    import twisted

    if getattr(twisted, "__datadog_patch", False):
        return

    Pin().onto(twisted)

    trace_utils.wrap("twisted.internet.defer", "Deferred.__init__", deferred_init(twisted))
    trace_utils.wrap("twisted.internet.defer", "Deferred.callback", deferred_callback(twisted))
    trace_utils.wrap("twisted.internet.defer", "Deferred.errback", deferred_errback(twisted))
    trace_utils.wrap("twisted.internet.defer", "Deferred.addCallbacks", deferred_addCallbacks(twisted))
    trace_utils.wrap("twisted.enterprise.adbapi", "ConnectionPool.__init__", connectionpool___init__(twisted))
    trace_utils.wrap("twisted.enterprise.adbapi", "ConnectionPool.runQuery", connectionpool_runquery(twisted))
    trace_utils.wrap(
        "twisted.python.threadpool", "ThreadPool.callInThreadWithCallback", threadpool_callInThreadWithCallback(twisted)
    )
    trace_utils.wrap("twisted.web.client", "HTTPClientFactory.__init__", httpclientfactory___init__(twisted))
    trace_utils.wrap("twisted.internet", "reactor.callLater", reactor_callLater(twisted))

    setattr(twisted, "__datadog_patch", True)


def unpatch():
    import twisted

    if not getattr(twisted, "__datadog_patch", False):
        return

    trace_utils.unwrap(twisted.internet.defer.Deferred, "__init__")
    trace_utils.unwrap(twisted.internet.defer.Deferred, "callback")
    trace_utils.unwrap(twisted.internet.defer.Deferred, "errback")
    trace_utils.unwrap(twisted.internet.defer.Deferred, "addCallbacks")
    trace_utils.unwrap(twisted.enterprise.adbapi.ConnectionPool, "__init__")
    trace_utils.unwrap(twisted.enterprise.adbapi.ConnectionPool, "runQuery")
    trace_utils.unwrap(twisted.python.threadpool.ThreadPool, "callInThreadWithCallback")
    trace_utils.unwrap(twisted.web.client.HTTPClientFactory, "__init__")
    trace_utils.unwrap(twisted.internet.reactor, "callLater")

    setattr(twisted, "__datadog_patch", False)
