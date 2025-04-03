r"""
The Tornado integration traces all ``RequestHandler`` defined in a Tornado web application.
Auto instrumentation is available using the ``patch`` function that **must be called before**
importing the tornado library.

The following is an example::

    # patch before importing tornado and concurrent.futures
    from ddtrace.trace import tracer, patch
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

If you are overriding the ``on_finish`` or ``log_exception`` methods on a
``RequestHandler``, you will need to call the super method to ensure the
tracer's patched methods are called::

    class MainHandler(tornado.web.RequestHandler):
        @tornado.gen.coroutine
        def get(self):
            self.write("Hello, world")

        def on_finish(self):
            super(MainHandler, self).on_finish()
            # do other clean-up

        def log_exception(self, typ, value, tb):
            super(MainHandler, self).log_exception(typ, value, tb)
            # do other logging

Tornado settings can be used to change some tracing configuration, like::

    settings = {
        'datadog_trace': {
            'default_service': 'my-tornado-app',
            'tags': {'env': 'production'},
            'distributed_tracing': False,
        },
    }

    app = tornado.web.Application([
        (r'/', MainHandler),
    ], **settings)

The available settings are:

* ``default_service`` (default: `tornado-web`): set the service name used by the tracer. Usually
  this configuration must be updated with a meaningful name. Can also be configured via the
  ``DD_SERVICE`` environment variable.
* ``tags`` (default: `{}`): set global tags that should be applied to all spans.
* ``enabled`` (default: `True`): define if the tracer is enabled or not. If set to `false`, the
  code is still instrumented but no spans are sent to the APM agent.
* ``distributed_tracing`` (default: `None`): enable distributed tracing if this is called
  remotely from an instrumented application. Overrides the integration config which is configured via the
  ``DD_TORNADO_DISTRIBUTED_TRACING`` environment variable.
  We suggest to enable it only for internal services where headers are under your control.
* ``agent_hostname`` (default: `localhost`): define the hostname of the APM agent.
* ``agent_port`` (default: `8126`): define the port of the APM agent.
* ``settings`` (default: ``{}``): Tracer extra settings used to change, for instance, the filtering behavior.
"""
from ddtrace.contrib.internal.tornado.stack_context import TracerStackContext
from ddtrace.contrib.internal.tornado.stack_context import context_provider
from ddtrace.contrib.internal.tornado.stack_context import run_with_trace_context


__all__ = [
    "context_provider",
    "run_with_trace_context",
    "TracerStackContext",
]
