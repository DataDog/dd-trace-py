import tornado.web

from .compat import sleep


class SuccessHandler(tornado.web.RequestHandler):
    @tornado.gen.coroutine
    def get(self):
        self.write('OK')


class NestedHandler(tornado.web.RequestHandler):
    @tornado.gen.coroutine
    def get(self):
        tracer = self.settings['datadog_trace']['tracer']
        with tracer.trace('tornado.sleep'):
            yield sleep(0.05)
        self.write('OK')


class NestedWrapHandler(tornado.web.RequestHandler):
    @tornado.gen.coroutine
    def get(self):
        tracer = self.settings['datadog_trace']['tracer']

        # define a wrapped coroutine: this approach
        # is only for testing purpose
        @tracer.wrap('tornado.coro')
        @tornado.gen.coroutine
        def coro():
            yield sleep(0.05)

        yield coro()
        self.write('OK')


class ExceptionHandler(tornado.web.RequestHandler):
    @tornado.gen.coroutine
    def get(self):
        raise Exception('Ouch!')


class HTTPExceptionHandler(tornado.web.RequestHandler):
    @tornado.gen.coroutine
    def get(self):
        raise tornado.web.HTTPError(status_code=410, log_message='Gone', reason='No reason')


class SyncSuccessHandler(tornado.web.RequestHandler):
    def get(self):
        self.write('OK')


class SyncExceptionHandler(tornado.web.RequestHandler):
    def get(self):
        raise Exception('Ouch!')


def make_app():
    """
    Create a Tornado web application, useful to test
    different behaviors.
    """
    return tornado.web.Application([
        (r'/success/', SuccessHandler),
        (r'/nested/', NestedHandler),
        (r'/nested_wrap/', NestedWrapHandler),
        (r'/exception/', ExceptionHandler),
        (r'/http_exception/', HTTPExceptionHandler),
        # synchronous handlers
        (r'/sync_success/', SyncSuccessHandler),
        (r'/sync_exception/', SyncExceptionHandler),
    ])
