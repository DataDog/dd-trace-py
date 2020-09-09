"""
"""
import contextvars
import functools

from ddtrace import config, Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY, SPAN_MEASURED_KEY
from ddtrace.ext import http, SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import HTTPPropagator
from .. import trace_utils


config._add("cyclone", dict(_default_service="cyclone", distributed_tracing_enabled=True,))

config._add("cyclone_client", dict(_default_service="cyclone_client", distributed_tracing_enabled=True,))


log = get_logger(__name__)


@trace_utils.with_traced_module
def traced__execute(cyclone, pin, func, instance, args, kwargs):
    try:
        if config.cyclone.distributed_tracing_enabled:
            propagator = HTTPPropagator()
            context = propagator.extract(instance.request.headers)
            if context.trace_id:
                pin.tracer.context_provider.activate(context)

        span = pin.tracer.trace(
            "cyclone.request", service=trace_utils.int_service(pin, config.cyclone), span_type=SpanTypes.WEB
        )

        span.set_tag(SPAN_MEASURED_KEY)
        analytics_sr = config.cyclone.get_analytics_sample_rate(use_global_config=True)
        if analytics_sr is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, analytics_sr)

        req = instance.request
        trace_utils.store_request_headers(req.headers, span, config.cyclone)

        setattr(req, "__datadog_span", span)
    finally:
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced_on_finish(cyclone, pin, func, instance, args, kwargs):
    req = instance.request
    span = getattr(req, "__datadog_span")
    if not span:
        log.warning("no span found on request")
        return func(*args, **kwargs)

    try:
        cls = instance.__class__
        # TODO: resource should be <http method> <route>
        span.resource = "{}.{}".format(cls.__module__, cls.__name__)
        span.set_tag("http.method", req.method)
        status_code = instance.get_status()
        if 500 <= int(status_code) < 600:
            span.error = 1
        span.set_tag("http.status_code", status_code)
        span.set_tag("http.url", req.full_url().rsplit("?", 1)[0])
        trace_utils.store_response_headers(instance._headers, span, config.cyclone)
        if config.cyclone.http.trace_query_string:
            span.set_tag(http.QUERY_STRING, req.query)
        span.finish()
    except Exception:
        log.warning("error occurred in instrumentation", exc_info=True)
    finally:
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced__handle_request_exception(cyclone, pin, func, instance, args, kwargs):
    req = instance.request
    span = getattr(req, "__datadog_span")
    if not span:
        return func(*args, **kwargs)

    # The current stack should have the exception that is being handled as this
    # method is being called, so report traceback.
    try:
        exc = args[0]
        # If the exception is a twisted.python.Failure
        # then we can re-raise the exception to get the
        # exception info to report.
        if hasattr(exc, "raiseException"):
            try:
                exc.raiseException()
            except:  # noqa
                span.set_traceback()
        # Otherwise try to pull the exception out of the stack
        else:
            span.set_traceback()
    finally:
        return func(*args, **kwargs)


def deferred_init(func, instance, args, kwargs):
    ctx = contextvars.copy_context()
    instance.__ctx = ctx
    from ddtrace.internal.context_manager import _DD_CONTEXTVAR
    from ddtrace.context import Context

    _DD_CONTEXTVAR.set(Context())
    return func(*args, **kwargs)


def deferred_callback(func, instance, args, kwargs):
    callback = args[0] or kwargs.pop("callback")
    ctx = instance.__ctx

    @functools.wraps(callback)
    def _callback(*args, **kwargs):
        # contextvars throws a RuntimeError if the function that is run is
        # called recursively so skip if this is the case
        if ctx != contextvars.copy_context():
            return ctx.run(callback, *args, **kwargs)
        else:
            return callback(*args, **kwargs)

    newargs = list(args)
    newargs[0] = _callback
    return func(*tuple(newargs), **kwargs)


def patch():
    import cyclone

    if getattr(cyclone, "__datadog_patch", False):
        return

    Pin().onto(cyclone)
    trace_utils.wrap("cyclone.web", "RequestHandler._execute", traced__execute(cyclone))
    trace_utils.wrap("cyclone.web", "RequestHandler.on_finish", traced_on_finish(cyclone))
    trace_utils.wrap(
        "cyclone.web", "RequestHandler._handle_request_exception", traced__handle_request_exception(cyclone)
    )

    trace_utils.wrap("twisted.internet.defer", "Deferred.__init__", deferred_init)
    trace_utils.wrap("twisted.internet.defer", "Deferred.addCallbacks", deferred_callback)
    trace_utils.wrap("twisted.internet.defer", "Deferred.addCallback", deferred_callback)

    setattr(cyclone, "__datadog_patch", True)


def unpatch():
    import cyclone

    if not getattr(cyclone, "__datadog_patch", False):
        return

    trace_utils.unwrap(cyclone.web.RequestHandler, "_execute")
    trace_utils.unwrap(cyclone.web.RequestHandler, "on_finish")

    setattr(cyclone, "__datadog_patch", False)
