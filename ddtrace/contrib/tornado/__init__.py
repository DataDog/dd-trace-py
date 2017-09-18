"""
The Tornado integration traces all ``RequestHandler`` defined in a Tornado web application.
Auto instrumentation is available using the ``patch`` function that **must be called before**
importing the tornado library. The following is an example::

    # patch before importing tornado
    from ddtrace import tracer, patch
    patch(tornado=True)

    import tornado.web
    import tornado.gen
    import tornado.ioloop

    # create your handlers
    class MainHandler(tornado.web.RequestHandler):
        @tornado.gen.coroutine
        def get(self):
            self.write("Hello, world")

    # create your application
    app = tornado.web.Application([
        (r'/', MainHandler),
    ])

    # and run it as usual
    app.listen(8888)
    tornado.ioloop.IOLoop.current().start()

When any type of ``RequestHandler`` is hit, a request root span is automatically created. If
you want to trace more parts of your application, you can use the ``wrap()`` decorator and
the ``trace()`` method as usual::

    class MainHandler(tornado.web.RequestHandler):
        @tornado.gen.coroutine
        def get(self):
            yield self.notify()
            yield self.blocking_method()
            with tracer.trace('tornado.before_write') as span:
                # trace more work in the handler

        @tracer.wrap('tornado.executor_handler')
        @tornado.concurrent.run_on_executor
        def blocking_method(self):
            # do something expensive

        @tracer.wrap('tornado.notify', service='tornado-notification')
        @tornado.gen.coroutine
        def notify(self):
            # do something

Tornado settings can be used to change some tracing configuration, like::

    settings = {
        'datadog_trace': {
            'default_service': 'my-tornado-app',
            'tags': {'env': 'production'},
        },
    }

    app = tornado.web.Application([
        (r'/', MainHandler),
    ], **settings)

The available settings are:

* ``default_service`` (default: `tornado-web`): set the service name used by the tracer. Usually
  this configuration must be updated with a meaningful name.
* ``tags`` (default: `{}`): set global tags that should be applied to all spans.
* ``enabled`` (default: `true`): define if the tracer is enabled or not. If set to `false`, the
  code is still instrumented but no spans are sent to the APM agent.
* ``agent_hostname`` (default: `localhost`): define the hostname of the APM agent.
* ``agent_port`` (default: `8126`): define the port of the APM agent.
"""
from ..util import require_modules


required_modules = ['tornado']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        # alias for API compatibility
        from .stack_context import run_with_trace_context, TracerStackContext
        context_provider = TracerStackContext.current_context

        from .patch import patch, unpatch

        __all__ = [
            'patch',
            'unpatch',
            'context_provider',
            'run_with_trace_context',
            'TracerStackContext',
        ]
