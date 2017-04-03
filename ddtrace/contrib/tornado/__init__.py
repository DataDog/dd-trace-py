"""
The Tornado integration traces all ``RequestHandler`` defined in a Tornado web application.
Auto instrumentation is available using the ``trace_app`` function as follows::

    import tornado.web
    import tornado.gen
    import tornado.ioloop

    from ddtrace import tracer
    from ddtrace.contrib.tornado import trace_app

    # create your handlers
    class MainHandler(tornado.web.RequestHandler):
        @tornado.gen.coroutine
        def get(self):
            self.write("Hello, world")

    # create your application
    app = tornado.web.Application([
        (r'/', MainHandler),
    ])

    # trace your application before the execution
    trace_app(app, tracer, service='tornado-site')

    # and run it as usual
    app.listen(8888)
    tornado.ioloop.IOLoop.current().start()

When a ``RequestHandler`` is hit, a request root span is automatically created and if you want
to trace more parts of your application, you can use both the ``Tracer.wrap()`` decorator and
the ``Tracer.trace()`` method like usual::

    class MainHandler(tornado.web.RequestHandler):
        @tornado.gen.coroutine
        def get(self):
            yield self.notify()
            with tracer.trace('tornado.post_notify') as span:
                # do more work

        @tracer.wrap('tornado.notify', service='tornado-notification')
        @tornado.gen.coroutine
        def notify(self):
            # do something
"""
from ..util import require_modules


required_modules = ['tornado']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .stack_context import run_with_trace_context, TracerStackContext
        from .middlewares import trace_app, untrace_app

        # alias for API compatibility
        context_provider = TracerStackContext.current_context

        __all__ = [
            'context_provider',
            'run_with_trace_context',
            'TracerStackContext',
            'trace_app',
            'untrace_app',
        ]
