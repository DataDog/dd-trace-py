"""
"""
import functools

from ddtrace import config, Pin
from ddtrace.compat import contextvars
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY, SPAN_MEASURED_KEY
from ddtrace.ext import http, SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import HTTPPropagator
from .. import trace_utils


config._add("cyclone", dict(_default_service="cyclone", distributed_tracing_enabled=True,))

config._add("cyclone_client", dict(_default_service="cyclone_client", distributed_tracing_enabled=True,))


log = get_logger(__name__)


@trace_utils.with_traced_module
def traced_requesthandler__init__(cyclone, pin, func, instance, args, kwargs):
    # Note that default is not an http method, but is the method that will be
    # called by Cyclone if a handler is not found.
    for method_name in ["get", "post", "options", "delete", "head", "default"]:
        if hasattr(instance, method_name) and method_name in vars(instance.__class__):
            method = getattr(instance, method_name)
            if not trace_utils.iswrapped(method):
                trace_utils.wrap(
                    instance, method_name, trace_utils.traced_func(cyclone, "cyclone.request.%s" % method_name)
                )
    return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced_requesthandler_execute_handler(cyclone, pin, func, instance, args, kwargs):
    try:
        if config.cyclone.distributed_tracing_enabled:
            propagator = HTTPPropagator()
            context = propagator.extract(instance.request.headers)
            if context.trace_id:
                pin.tracer.context_provider.activate(context)

        span = pin.tracer.trace(
            "cyclone.request", service=trace_utils.int_service(pin, config.cyclone), span_type=SpanTypes.WEB
        )
        cls = instance.__class__
        # TODO: resource should be <http method> <route>
        handler = "{}.{}".format(cls.__module__, cls.__name__)
        span.resource = handler
        span.set_tag(SPAN_MEASURED_KEY)
        span.set_tag("cyclone.handler", handler)
        analytics_sr = config.cyclone.get_analytics_sample_rate(use_global_config=True)
        if analytics_sr is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, analytics_sr)

        req = instance.request
        trace_utils.store_request_headers(req.headers, span, config.cyclone)

        setattr(req, "__datadog_span", span)
    except Exception:
        log.warning("error occurred in instrumentation", exc_info=True)
    finally:
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced_requesthandler_on_finish(cyclone, pin, func, instance, args, kwargs):
    req = instance.request
    span = getattr(req, "__datadog_span", None)
    if not span:
        log.warning("no span found on request")
        return func(*args, **kwargs)

    try:
        span.set_tag("http.method", req.method)
        status_code = instance.get_status()
        if 500 <= int(status_code) < 600:
            span.error = 1
        span.set_tag("http.status_code", status_code)
        span.set_tag("http.url", req.full_url().rsplit("?", 1)[0])
        trace_utils.store_response_headers(instance._headers, span, config.cyclone)
        if config.cyclone.http.trace_query_string:
            span.set_tag(http.QUERY_STRING, req.query)
    except Exception:
        log.warning("error occurred in instrumentation", exc_info=True)
    finally:
        try:
            return func(*args, **kwargs)
        finally:
            span.finish()


@trace_utils.with_traced_module
def traced_requesthandler_handle_request_exception(cyclone, pin, func, instance, args, kwargs):
    req = instance.request
    span = getattr(req, "__datadog_span", None)
    if not span:
        log.debug("no span found on request")
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


@trace_utils.with_traced_module
def traced_requesthandler_render_string(cyclone, pin, func, instance, args, kwargs):
    template_name = args[0] if len(args) else kwargs.get("template_name")
    with pin.tracer.trace("cyclone.request.render_string", span_type=SpanTypes.TEMPLATE) as span:
        span.set_tag("template_name", template_name)
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced_uimodule_render(cyclone, pin, func, instance, args, kwargs):
    with pin.tracer.trace("cyclone.uimodule.render", span_type=SpanTypes.TEMPLATE):
        return func(*args, **kwargs)


@trace_utils.with_traced_module
def traced_template_generate(cyclone, pin, func, instance, args, kwargs):
    with pin.tracer.trace("cyclone.template.generate", span_type=SpanTypes.TEMPLATE):
        return func(*args, **kwargs)


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

    newargs = list(args)
    newargs[0] = _callback
    return func(*tuple(newargs), **kwargs)


def patch():
    import cyclone

    if getattr(cyclone, "__datadog_patch", False):
        return

    Pin().onto(cyclone)
    trace_utils.wrap("cyclone.web", "RequestHandler.__init__", traced_requesthandler__init__(cyclone))
    trace_utils.wrap("cyclone.web", "RequestHandler._execute_handler", traced_requesthandler_execute_handler(cyclone))
    trace_utils.wrap("cyclone.web", "RequestHandler.on_finish", traced_requesthandler_on_finish(cyclone))
    trace_utils.wrap(
        "cyclone.web",
        "RequestHandler._handle_request_exception",
        traced_requesthandler_handle_request_exception(cyclone),
    )
    trace_utils.wrap("cyclone.web", "RequestHandler.render_string", traced_requesthandler_render_string(cyclone))
    trace_utils.wrap("cyclone.web", "UIModule.render", traced_uimodule_render(cyclone))
    trace_utils.wrap("cyclone.template", "Template.generate", traced_template_generate(cyclone))

    trace_utils.wrap("twisted.internet.defer", "Deferred.__init__", deferred_init(cyclone))
    trace_utils.wrap("twisted.internet.defer", "Deferred.addCallbacks", deferred_callback(cyclone))
    trace_utils.wrap("twisted.internet.defer", "Deferred.addCallback", deferred_callback(cyclone))

    setattr(cyclone, "__datadog_patch", True)


def unpatch():
    import cyclone

    if not getattr(cyclone, "__datadog_patch", False):
        return

    trace_utils.unwrap(cyclone.web.RequestHandler, "__init__")
    trace_utils.unwrap(cyclone.web.RequestHandler, "_execute_handler")
    trace_utils.unwrap(cyclone.web.RequestHandler, "render_string")
    trace_utils.unwrap(cyclone.web.UIModule, "render")
    trace_utils.unwrap(cyclone.template.Template, "generate")
    trace_utils.unwrap(cyclone.web.RequestHandler, "on_finish")

    setattr(cyclone, "__datadog_patch", False)
