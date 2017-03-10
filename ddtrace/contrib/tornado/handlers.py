from wrapt import function_wrapper

from .settings import CONFIG_KEY, REQUEST_CONTEXT_KEY, REQUEST_SPAN_KEY
from .stack_context import TracerStackContext
from ...ext import http


@function_wrapper
def wrap_execute(func, handler, args, kwargs):
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
def wrap_on_finish(func, handler, args, kwargs):
    """
    Wrap the ``RequestHandler.on_finish`` method. This is the last executed method
    after the response has been sent, and it's used to retrieve and close the
    current request span (if available).
    """
    request = handler.request
    request_span = getattr(request, REQUEST_SPAN_KEY, None)
    if request_span:
        # TODO: check if this works and doesn't spam users
        request_span.resource = request.path
        request_span.set_tag('http.method', request.method)
        request_span.set_tag('http.status_code', handler.get_status())
        request_span.set_tag('http.url', request.uri)
        request_span.finish()

    return func(*args, **kwargs)


@function_wrapper
def wrap_log_exception(func, handler, args, kwargs):
    """
    Wrap the ``RequestHandler.log_exception``. This method is called when an
    Exception is not handled in the user code. In this case, we save the exception
    in the current active span. If the Tornado ``Finish`` exception is raised, this wrapper
    will not be called because ``Finish`` is not an exception.
    """
    # retrieve the current span
    settings = handler.settings[CONFIG_KEY]
    tracer = settings['tracer']
    current_span = tracer.current_span()

    # received arguments are: log_exception(self, typ, value, tb)
    current_span.set_exc_info(*args)
    return func(*args, **kwargs)
