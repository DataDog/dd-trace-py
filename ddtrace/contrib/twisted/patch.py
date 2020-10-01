import inspect
import functools
import sys

from ddtrace.vendor import six

from ddtrace import Pin, config
from ddtrace.compat import contextvars
from ddtrace.contrib import func_name
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
    if ddctx._current_span and ddctx.get_ctx_item("trace_deferreds", default=config.twisted.trace_all_deferreds):
        name = ddctx.get_ctx_item("deferred_name", None)
        if not name:
            # If a name isn't provided, go up two frames to get to the function that created the deferred
            # <fn we care about that creates the deferred>
            # wrapper (from with_traced_module)
            # Deferred.__init__
            # This wrapper (currentframe())
            # co_caller = inspect.currentframe().f_back.f_back.f_back.f_code
            # co_caller = inspect.currentframe().f_back.f_back.f_code
            # name = "twisted.%s" % co_caller.co_name
            stack = inspect.stack()
            locals = stack[2][0].f_locals
            method_name = stack[2][0].f_code.co_name
            if "self" in locals:
                cls = locals["self"].__class__.__name__
                name = "%s.%s.deferred" % (cls, method_name)
            else:
                name = "%s.deferred" % method_name

        span = pin.tracer.trace(name, service=trace_utils.int_service(pin, config.twisted))
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
        span.error = 1
        span.finish()

    return func(*args, **kwargs)


@trace_utils.with_traced_module
def deferred_addCallbacks(twisted, pin, func, instance, args, kwargs):
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
        ctx = instance.__ctx
        ddctx = instance.__ddctx

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
def connectionpool_runquery(twisted, pin, func, instance, args, kwargs):
    ctx = pin.tracer.get_call_context()
    with ctx.override_ctx_item("trace_deferreds", True):
        with ctx.override_ctx_item("deferred_name", "ConnectionPool.runQuery.deferred"):
            return func(*args, **kwargs)


@trace_utils.with_traced_module
def httpclientfactory___init__(twisted, pin, func, instance, args, kwargs):
    try:
        return func(*args, **kwargs)
    finally:
        # Note that HTTPClientFactory creates a Deferred in its init
        # so we want that to be the active span for when we set the distributed
        # tracing headers.
        ctx = pin.tracer.get_call_context()

        if config.twisted.distributed_tracing:
            # if len(args) > 4:
            #     headers = args[4]
            # else:
            #     headers = kwargs.setdefault("headers", {})
            propagator = HTTPPropagator()
            # propagator.inject(ctx, headers)
            propagator.inject(ctx, instance.headers)


def patch():
    import twisted

    if getattr(twisted, "__datadog_patch", False):
        return

    Pin().onto(twisted)

    trace_utils.wrap("twisted.internet.defer", "Deferred.__init__", deferred_init(twisted))
    trace_utils.wrap("twisted.internet.defer", "Deferred.callback", deferred_callback(twisted))
    trace_utils.wrap("twisted.internet.defer", "Deferred.errback", deferred_errback(twisted))
    trace_utils.wrap("twisted.internet.defer", "Deferred.addCallbacks", deferred_addCallbacks(twisted))
    trace_utils.wrap("twisted.enterprise.adbapi", "ConnectionPool.runQuery", connectionpool_runquery(twisted))
    trace_utils.wrap(
        "twisted.python.threadpool", "ThreadPool.callInThreadWithCallback", threadpool_callInThreadWithCallback(twisted)
    )
    trace_utils.wrap("twisted.web.client", "HTTPClientFactory.__init__", httpclientfactory___init__(twisted))

    setattr(twisted, "__datadog_patch", True)


def unpatch():
    import twisted

    if not getattr(twisted, "__datadog_patch", False):
        return

    trace_utils.unwrap(twisted.internet.defer.Deferred, "__init__")
    trace_utils.unwrap(twisted.internet.defer.Deferred, "callback")
    trace_utils.unwrap(twisted.internet.defer.Deferred, "errback")
    trace_utils.unwrap(twisted.internet.defer.Deferred, "addCallbacks")
    trace_utils.unwrap(twisted.enterprise.adbapi.ConnectionPool, "runQuery")
    trace_utils.unwrap(twisted.python.threadpool.ThreadPool, "callInThreadWithCallback")
    trace_utils.unwrap(twisted.web.client.HTTPClientFactory, "__init__")

    setattr(twisted, "__datadog_patch", False)
