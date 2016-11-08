"""

The tornado handler mixin adds tracing support to tornado applications. To use
it you add the mixin to your handlers before RequestHandler and set a
`tracer_service` class attribute.

By default the supplied value for service will be used and the resource will be
set to the full module path, classname and method, which can be override by a
`tracer_resource` attribute.


    from tornado.gen import coroutine, sleep
    from tornado.web import RequestHandler
    from ddtrace.contrib.tornado import TracerMixin


    class BaseHandler(TracerMixin, RequestHandler):
        tracer_service = 'demo'


    class IndexHandler(BaseHandler):

        @coroutine
        def get(self):
            with self.trace('sleeping') as span:
                sleep(1)
            self.write("Hello, world")

        def post(self):
            raise Exception('boom')


In the above example both both the get and post methods will have spans created
for them and the get method will also have a child span wrapping the sleep call.
"""

from ddtrace import Tracer, tracer
from ddtrace.buffer import SpanBuffer


class _FirstSpanBuffer(SpanBuffer):

    def __init__(self):
        self._span = None

    def set(self, span):
        if self._span is None:
            self._span = span
        elif span is None:
            self._span = None

    def get(self):
        return self._span


class TracerMixin(object):

    def initialize(self, *args, **kwargs):
        super(TracerMixin, self).initialize(*args, **kwargs)

        self._tracer = Tracer()
        self._tracer.span_buffer = _FirstSpanBuffer()
        # This is ugly, refactoring should help
        self._tracer.debug_logging = tracer.debug_logging
        self._tracer.enabled = tracer.enabled

    def prepare(self):
        super(TracerMixin, self).prepare()

        self._span = self._tracer.trace('web.request')

    def _handle_request_exception(self, exc):
        if hasattr(self, '_span'):
            self._span.set_traceback()
        super(TracerMixin, self)._handle_request_exception(exc)

    def on_finish(self):
        super(TracerMixin, self).on_finish()

        # setting this stuff here to do the work after the request has been
        # returned to the user so taht we can't impact its timing or success.
        self._span.service = self.tracer_service
        try:
            resource = self.tracer_resource
        except AttributeError:
            resource = '{}.{}:{}'.format(self.__module__,
                                         self.__class__.__name__,
                                         self.request.method.lower())
        self._span.resource = resource
        self._span.finish()

    def trace(self, *args, **kwargs):
        return self._tracer.trace(*args, **kwargs)
