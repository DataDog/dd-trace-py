from tornado.gen import coroutine
from tornado.web import Application
from tornado.stack_context import StackContext

from . import TracerStackContext, handlers
from ...ext import AppTypes


class TraceMiddleware(object):
    """
    Tornado middleware class that traces a Tornado ``HTTPServer`` instance
    so that the request_callback is wrapped in a ``TracerStackContext``.
    This middleware creates a root span for each request.
    """
    def __init__(self, http_server, tracer, service='tornado-web'):
        """
        Replace the default ``HTTPServer`` request callback with this
        class instance that is callable. If the given request callback
        is a Tornado ``Application``, all handlers are wrapped with
        tracing methods.
        """
        self._http_server = http_server
        self._tracer = tracer
        self._service = service
        # the default http_server callback must be preserved
        self._request_callback = http_server.request_callback

        # the tracer must use the right Context propagation
        self._tracer.configure(context_provider=TracerStackContext.current_context)

        # the middleware instance is callable so it behaves
        # like a regular request handler
        http_server.request_callback = self

        # configure the current service
        self._tracer.set_service_info(
            service=service,
            app='tornado',
            app_type=AppTypes.web,
        )

        if isinstance(self._request_callback, Application):
            # request handler is a Tornado web app that can be wrapped
            app = self._request_callback
            for _, specs in app.handlers:
                for spec in specs:
                    self._wrap_application_handlers(spec.handler_class)

    def _wrap_application_handlers(self, cls):
        """
        Wraps the Application class handler with tracing methods.
        """
        cls.on_finish = handlers.wrapper_on_finish(cls.on_finish)

    def __call__(self, request):
        """
        The class instance is callable and can be used in the Tornado ``HTTPServer``
        to handle incoming requests under the same ``TracerStackContext``.
        The current context and the root request span are attached to the request so
        that they can be used in the application code.
        """
        # attach the context to the request
        with TracerStackContext():
            setattr(request, 'datadog_context', self._tracer.get_call_context())
            # store the request handler so that it can be retrieved later
            request_span = self._tracer.trace('tornado.request_handler', service=self._service)
            setattr(request, '__datadog_request_span', request_span)
            return self._request_callback(request)
