from tornado.web import HTTPError

from ddtrace import config

from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...internal.utils import ArgumentError
from ...internal.utils import get_argument_value
from ..trace_utils import set_http_meta
from .constants import CONFIG_KEY
from .constants import REQUEST_SPAN_KEY
from .stack_context import TracerStackContext


def execute(func, handler, args, kwargs):
    """
    Wrap the handler execute method so that the entire request is within the same
    ``TracerStackContext``. This simplifies users code when the automatic ``Context``
    retrieval is used via ``Tracer.trace()`` method.
    """
    # retrieve tracing settings
    settings = handler.settings[CONFIG_KEY]
    tracer = settings["tracer"]
    service = settings["default_service"]
    distributed_tracing = settings["distributed_tracing"]

    with TracerStackContext():
        trace_utils.activate_distributed_headers(
            tracer, int_config=config.tornado, request_headers=handler.request.headers, override=distributed_tracing
        )

        # store the request span in the request so that it can be used later
        request_span = tracer.trace(
            "tornado.request",
            service=service,
            span_type=SpanTypes.WEB,
        )
        request_span.set_tag(SPAN_MEASURED_KEY)
        # set analytics sample rate
        # DEV: tornado is special case maintains separate configuration from config api
        analytics_enabled = settings["analytics_enabled"]
        if (config.analytics_enabled and analytics_enabled is not False) or analytics_enabled is True:
            request_span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, settings.get("analytics_sample_rate", True))

        setattr(handler.request, REQUEST_SPAN_KEY, request_span)

        return func(*args, **kwargs)


def on_finish(func, handler, args, kwargs):
    """
    Wrap the ``RequestHandler.on_finish`` method. This is the last executed method
    after the response has been sent, and it's used to retrieve and close the
    current request span (if available).
    """
    request = handler.request
    request_span = getattr(request, REQUEST_SPAN_KEY, None)
    if request_span:
        # use the class name as a resource; if an handler is not available, the
        # default handler class will be used so we don't pollute the resource
        # space here
        klass = handler.__class__
        request_span.resource = "{}.{}".format(klass.__module__, klass.__name__)
        set_http_meta(
            request_span,
            config.tornado,
            method=request.method,
            url=request.full_url().rsplit("?", 1)[0],
            status_code=handler.get_status(),
            query=request.query,
        )
        request_span.finish()

    return func(*args, **kwargs)


def log_exception(func, handler, args, kwargs):
    """
    Wrap the ``RequestHandler.log_exception``. This method is called when an
    Exception is not handled in the user code. In this case, we save the exception
    in the current active span. If the Tornado ``Finish`` exception is raised, this wrapper
    will not be called because ``Finish`` is not an exception.
    """
    # safe-guard: expected arguments -> log_exception(self, typ, value, tb)
    try:
        value = get_argument_value(args, kwargs, 1, "value")
    except ArgumentError:
        value = None

    if not value:
        return func(*args, **kwargs)

    # retrieve the current span
    tracer = handler.settings[CONFIG_KEY]["tracer"]
    current_span = tracer.current_span()

    if not current_span:
        return func(*args, **kwargs)

    if isinstance(value, HTTPError):
        # Tornado uses HTTPError exceptions to stop and return a status code that
        # is not a 2xx. In this case we want to check the status code to be sure that
        # only 5xx are traced as errors, while any other HTTPError exception is handled as
        # usual.
        if 500 <= value.status_code <= 599:
            current_span.set_exc_info(*args)
    else:
        # any other uncaught exception should be reported as error
        current_span.set_exc_info(*args)

    return func(*args, **kwargs)
