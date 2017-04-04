from wrapt import function_wrapper

from tornado.web import ErrorHandler, HTTPError

from .settings import CONFIG_KEY, REQUEST_CONTEXT_KEY, REQUEST_SPAN_KEY
from .stack_context import TracerStackContext
from ...ext import http


@function_wrapper
def _wrap_execute(func, handler, args, kwargs):
    """
    Wrap the handler execute method so that the entire request is within the same
    ``TracerStackContext``. This simplifies users code when the automatic ``Context``
    retrieval is used via ``Tracer.trace()`` method.
    """
    # retrieve tracing settings
    settings = handler.settings[CONFIG_KEY]
    tracer = settings['tracer']
    service = settings['service']

    with TracerStackContext():
        # attach the context to the request
        setattr(handler.request, REQUEST_CONTEXT_KEY, tracer.get_call_context())

        # store the request span in the request so that it can be used later
        request_span = tracer.trace(
            'tornado.request',
            service=service,
            span_type=http.TYPE
        )
        setattr(handler.request, REQUEST_SPAN_KEY, request_span)

        return func(*args, **kwargs)


@function_wrapper
def _wrap_on_finish(func, handler, args, kwargs):
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
        request_span.resource = handler.__class__.__name__
        request_span.set_tag('http.method', request.method)
        request_span.set_tag('http.status_code', handler.get_status())
        request_span.set_tag('http.url', request.uri)
        request_span.finish()

    return func(*args, **kwargs)


@function_wrapper
def _wrap_log_exception(func, handler, args, kwargs):
    """
    Wrap the ``RequestHandler.log_exception``. This method is called when an
    Exception is not handled in the user code. In this case, we save the exception
    in the current active span. If the Tornado ``Finish`` exception is raised, this wrapper
    will not be called because ``Finish`` is not an exception.
    """
    # safe-guard: expected arguments -> log_exception(self, typ, value, tb)
    value = args[1] if len(args) == 3 else None
    if not value:
        return func(*args, **kwargs)

    # retrieve the current span
    tracer = handler.settings[CONFIG_KEY]['tracer']
    current_span = tracer.current_span()

    if isinstance(value, HTTPError):
        # Tornado uses HTTPError exceptions to stop and return a status code that
        # is not a 2xx. In this case we want to trace as errorsbe sure that only 5xx
        # errors are traced as errors, while any other HTTPError exceptions are handled as
        # usual.
        if 500 < value.status_code < 599:
            current_span.set_exc_info(*args)
    else:
        # any other uncaught exception should be reported as error
        current_span.set_exc_info(*args)

    return func(*args, **kwargs)


def wrap_methods(handler_class):
    """
    Shortcut that wraps all methods of the given class handler so that they're traced.
    """
    # safe-guard: ensure that the handler class is patched once; it's possible
    # that the same handler is used in different endpoints
    if getattr(handler_class, '__datadog_trace', False):
        return
    setattr(handler_class, '__datadog_trace', True)

    # handlers for the request span
    handler_class._execute = _wrap_execute(handler_class._execute)
    handler_class.on_finish = _wrap_on_finish(handler_class.on_finish)
    # handlers for exceptions
    handler_class.log_exception = _wrap_log_exception(handler_class.log_exception)


def unwrap_methods(handler_class):
    """
    Shortcut that unwraps all methods of the given class handler so that they aren't traced.
    """
    # safe-guard: ensure that the handler class is patched once; it's possible
    # that the same handler is used in different endpoints
    if not getattr(handler_class, '__datadog_trace', False):
        return

    # handlers for the request span
    handler_class._execute = handler_class._execute.__wrapped__
    handler_class.on_finish = handler_class.on_finish.__wrapped__
    # handlers for exceptions
    handler_class.log_exception = handler_class.log_exception.__wrapped__

    # clear the attribute
    delattr(handler_class, '__datadog_trace')


class TracerErrorHandler(ErrorHandler):
    """
    Error handler class that is used to trace Tornado errors when the framework
    invokes the default handler. The class handles errors like the default
    ``ErrorHandler``, while tracing the execution and the result.
    """
    pass


wrap_methods(TracerErrorHandler)
