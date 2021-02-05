import functools
import sys

from ddtrace.vendor import six

from ddtrace import Pin, config
from ddtrace.compat import contextvars
from ddtrace.contrib import func_name
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.ext import errors
from ddtrace.propagation import http as http_prop
from ddtrace.utils.formats import asbool, get_env

from .. import trace_utils


config._add(
    "twisted",
    dict(
        _default_service="twisted",
        distributed_tracing=asbool(get_env("twisted", "distributed_tracing", default=True)),
        split_by_domain=asbool(get_env("twisted", "split_by_domain", default=False)),
    ),
)


@trace_utils.with_traced_module
def deferred_init(twisted, pin, func, instance, args, kwargs):
    """Patch the Deferred initializer to add contextvars support.

    Tracing deferreds is tricky as usually the deferred itself has little
    meaning. Instead it usually _represents_ work that is done outside of
    the deferred.

    In order to trace deferreds the context is copied when the deferred is
    initialized. This context will contain any span that was active at the
    time the deferred was created.

    The context is then used to run the callbacks of the deferred. This
    enables a trace to continue through deferred callback chains which can
    be thought of as synchronous execution.
    """
    ctx = contextvars.copy_context()
    setattr(instance, "__ctx", ctx)
    return func(*args, **kwargs)


@trace_utils.with_traced_module
def deferred_callback(twisted, pin, func, instance, args, kwargs):
    """Patch the Deferred.callback method.

    This method is used to invoke the callbacks of the deferred. For tracing
    this is used as the opportunity to activate the context stored in the
    initializer. This can be done as all callbacks are executed synchronously.

    Any span attached to the deferred is finished.

    From the Twisted docstring for callback::
        An instance of L{Deferred} may only have either L{callback} or
        L{errback} called on it, and only once.

    So span.finish() must be called in both the callback and errback patches.
    """
    ctx = instance.__ctx
    if hasattr(instance, "__ddspan"):
        span = instance.__ddspan
        ctx.run(pin.tracer.activate, span)

        if span and not span.finished:
            for n, cb in enumerate(instance.callbacks):
                # cb is a tuple of tuples:
                # (
                #   (callback, callbackArgs, callbackKWArgs),
                #   (errback, errbackArgs, errbackKWArgs)
                # )
                span.set_tag("callback.%d" % n, func_name(cb[0][0]))
                span.set_tag("errback.%d" % n, func_name(cb[1][0]))

            # Have to call span.finish in the context as that's where the span
            # is active.
            if getattr(instance, "__ddauto", True):
                ctx.run(span.finish)
    return ctx.run(func, *args, **kwargs)


@trace_utils.with_traced_module
def deferred_errback(twisted, pin, func, instance, args, kwargs):
    """Patch the Deferred.errback method.

    This method is called when an exception is raised in the deferred. It
    passes a Failure object to the errbacks of the deferred.
    """
    ctx = instance.__ctx
    if hasattr(instance, "__ddspan"):
        span = instance.__ddspan
        ctx.run(pin.tracer.activate, span)
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
            if getattr(instance, "__ddauto", True):
                ctx.run(span.finish)
    return func(*args, **kwargs)


@trace_utils.with_traced_module
def threadpool_callInThreadWithCallback(twisted, pin, func, instance, args, kwargs):
    f = args[1]
    ctx = contextvars.copy_context()

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        return ctx.run(f, *args, **kwargs)

    newargs = list(args)
    newargs[1] = wrapper
    return func(*newargs, **kwargs)


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
def connectionpool_runinteraction(twisted, pin, func, instance, args, kwargs):
    with pin.tracer.trace("twisted.db"):
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def httpclientfactory___init__(twisted, pin, func, instance, args, kwargs):
    func(*args, **kwargs)

    d = getattr(instance, "deferred", None)
    if d:
        span = d.ddtrace("twisted.http")
        if config.twisted.distributed_tracing:
            propagator = http_prop.HTTPPropagator()
            propagator.inject(span.context, instance.headers)


@trace_utils.with_traced_module
def agent_request(twisted, pin, func, instance, args, kwargs):
    span = pin.tracer.trace("twisted.agent.request")
    ctx = pin.tracer.get_call_context()
    if len(args) > 2:
        headers = args[2]
        if headers is None:
            headers = twisted.web.http_headers.Headers()
            newargs = list(args)
            newargs[2] = headers
            args = tuple(newargs)
    elif "headers" in kwargs:
        headers = kwargs.get("headers")
        if headers is None:
            headers = twisted.web.http_headers.Headers()
            kwargs["headers"] = headers
    else:
        headers = twisted.web.http_headers.Headers()
        kwargs["headers"] = headers

    headers.addRawHeader(http_prop.HTTP_HEADER_TRACE_ID, str(ctx.trace_id))
    headers.addRawHeader(http_prop.HTTP_HEADER_PARENT_ID, str(ctx.span_id))

    if ctx.sampling_priority:
        headers.addRawHeader(http_prop.HTTP_HEADER_SAMPLING_PRIORITY, str(ctx.sampling_priority))
    if ctx.dd_origin:
        headers.addRawHeader(http_prop.HTTP_HEADER_ORIGIN, str(ctx.dd_origin))

    d = func(*args, **kwargs)

    def finish_span(_):
        span.finish()
        return _

    d.addCallback(finish_span)
    return d


@trace_utils.with_traced_module
def reactor_callLater(twisted, pin, func, instance, args, kwargs):
    fn = args[1]
    ctx = contextvars.copy_context()

    @functools.wraps(fn)
    def traced_fn(*args, **kwargs):
        return ctx.run(fn, *args, **kwargs)

    newargs = list(args)
    newargs[1] = traced_fn
    return func(*newargs, **kwargs)


@trace_utils.with_traced_module
def _deferred_trace(twisted, pin, func, self, args, kwargs):
    auto_finish = kwargs.pop("auto_finish", True)
    if "child_of" not in kwargs:
        kwargs["child_of"] = pin.tracer.active()
    # Activation is never desired when working with deferreds as the context in
    # which the deferred is created is not necessarily the context in which the
    # work is performed.
    kwargs["activate"] = False
    span = kwargs.pop("span", pin.tracer.start_span(*args, **kwargs))
    self.__ddspan = span
    self.__ddauto = auto_finish
    return span


def patch():
    import twisted

    if getattr(twisted, "__datadog_patch", False):
        return

    Pin().onto(twisted)

    import twisted.internet.defer

    # Bit of a hack so we can use the conventional trace_utils.wrap to define the method.
    twisted.internet.defer.Deferred.ddtrace = lambda self: None
    trace_utils.wrap("twisted.internet.defer", "Deferred.ddtrace", _deferred_trace(twisted))
    trace_utils.wrap("twisted.internet.defer", "Deferred.__init__", deferred_init(twisted))
    trace_utils.wrap("twisted.internet.defer", "Deferred.callback", deferred_callback(twisted))
    trace_utils.wrap("twisted.internet.defer", "Deferred.errback", deferred_errback(twisted))
    trace_utils.wrap(
        "twisted.python.threadpool", "ThreadPool.callInThreadWithCallback", threadpool_callInThreadWithCallback(twisted)
    )
    trace_utils.wrap("twisted.enterprise.adbapi", "ConnectionPool.__init__", connectionpool___init__(twisted))
    trace_utils.wrap(
        "twisted.enterprise.adbapi", "ConnectionPool._runInteraction", connectionpool_runinteraction(twisted)
    )
    trace_utils.wrap("twisted.web.client", "HTTPClientFactory.__init__", httpclientfactory___init__(twisted))
    trace_utils.wrap("twisted.web.client", "Agent.request", agent_request(twisted))
    trace_utils.wrap("twisted.internet", "reactor.callLater", reactor_callLater(twisted))

    setattr(twisted, "__datadog_patch", True)


def unpatch():
    import twisted

    if not getattr(twisted, "__datadog_patch", False):
        return

    trace_utils.unwrap(twisted.internet.defer.Deferred, "__init__")
    trace_utils.unwrap(twisted.internet.defer.Deferred, "callback")
    trace_utils.unwrap(twisted.internet.defer.Deferred, "errback")
    trace_utils.unwrap(twisted.python.threadpool.ThreadPool, "callInThreadWithCallback")
    trace_utils.unwrap(twisted.enterprise.adbapi.ConnectionPool, "__init__")
    trace_utils.unwrap(twisted.web.client.HTTPClientFactory, "__init__")
    trace_utils.unwrap(twisted.web.client.Agent, "request")
    trace_utils.unwrap(twisted.internet.reactor, "callLater")

    setattr(twisted, "__datadog_patch", False)
