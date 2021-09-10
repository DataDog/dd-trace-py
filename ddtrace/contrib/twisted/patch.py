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
from ddtrace.propagation.http import HTTP_HEADER_TRACE_ID
from ddtrace.propagation.http import HTTP_HEADER_PARENT_ID
from ddtrace.propagation.http import HTTP_HEADER_SAMPLING_PRIORITY
from ddtrace.propagation.http import HTTP_HEADER_ORIGIN
from ddtrace.propagation.http import HTTPPropagator

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
    # Create a new context for this Deferred
    ctx = contextvars.copy_context()
    instance.__ctx = ctx
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
        return ctx.run(callback, *args, **kwargs)

    newargs = list(args)
    newargs[0] = _callback
    return func(*tuple(newargs), **kwargs)


@trace_utils.with_traced_module
def threadpool_callInThreadWithCallback(twisted, pin, func, instance, args, kwargs):
    fn = args[1]
    ctx = contextvars.copy_context()

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return ctx.run(fn, *args, **kwargs)

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
    span = pin.tracer.trace("twisted.runQuery")

    def _finish_span(_):
        span.finish()
        return _

    d = func(*args, **kwargs)
    d.addCallback(_finish_span)
    return d


@trace_utils.with_traced_module
def httpclientfactory___init__(twisted, pin, func, instance, args, kwargs):
    try:
        return func(*args, **kwargs)
    finally:
        span = pin.tracer.trace("http.request")

        def _finish_span(_):
            span.finish()
            return _

        instance.deferred.addCallback(_finish_span)
        if config.twisted.distributed_tracing:
            HTTPPropagator.inject(span.context, instance.headers)


@trace_utils.with_traced_module
def agent_request(twisted, pin, func, instance, args, kwargs):
    s = pin.tracer.trace("twisted.agent.request")
    ctx = pin.tracer.current_trace_context()
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

    headers.addRawHeader(HTTP_HEADER_TRACE_ID, str(ctx.trace_id))
    headers.addRawHeader(HTTP_HEADER_PARENT_ID, str(ctx.span_id))

    if ctx.sampling_priority:
        headers.addRawHeader(HTTP_HEADER_SAMPLING_PRIORITY, str(s.context.sampling_priority))
    if ctx._dd_origin:
        headers.addRawHeader(HTTP_HEADER_ORIGIN, str(ctx.dd_origin))

    d = func(*args, **kwargs)

    def finish_span(_):
        s.finish()
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
    trace_utils.unwrap(twisted.internet.defer.Deferred, "addCallbacks")
    trace_utils.unwrap(twisted.enterprise.adbapi.ConnectionPool, "__init__")
    trace_utils.unwrap(twisted.enterprise.adbapi.ConnectionPool, "runQuery")
    trace_utils.unwrap(twisted.python.threadpool.ThreadPool, "callInThreadWithCallback")
    trace_utils.unwrap(twisted.web.client.HTTPClientFactory, "__init__")
    trace_utils.unwrap(twisted.web.client.Agent, "request")
    trace_utils.unwrap(twisted.internet.reactor, "callLater")

    setattr(twisted, "__datadog_patch", False)
