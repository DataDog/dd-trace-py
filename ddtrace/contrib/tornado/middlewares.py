from tornado.web import Application
from tornado.stack_context import StackContext

from . import handlers
from .stack_context import ContextManager
from ...ext import AppTypes


class TraceMiddleware(object):
    """
    TODO
    """
    def __init__(self, http_server, tracer, service='tornado-web'):
        """
        TODO
        """
        self._http_server = http_server
        self._tracer = tracer
        self._service = service
        # the default http_server callback must be preserved
        self._request_callback = http_server.request_callback

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
            # request handler is a Tornado web app and we can safely wrap it
            app = self._request_callback
            for _, specs in app.handlers:
                for spec in specs:
                    self._wrap_application_handlers(spec.handler_class)

    def _wrap_application_handlers(self, cls):
        """
        TODO: wraps Application handlers
        """
        cls.on_finish = handlers.wrapper_on_finish(cls.on_finish)

    def __call__(self, request):
        """
        TODO: wraps only the default execution with a ContextManager()
        """
        with StackContext(lambda: ContextManager()):
            # TODO: attach the request span for this async Context
            request_span = self._tracer.trace('tornado.request_handler')
            ctx = ContextManager.current_context()
            ctx._request_span = request_span
            return self._request_callback(request)
