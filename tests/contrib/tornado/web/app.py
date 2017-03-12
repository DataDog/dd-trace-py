import os

import tornado.web

from .compat import sleep


BASE_DIR = os.path.dirname(os.path.realpath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'statics')


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
        raise tornado.web.HTTPError(status_code=501, log_message='unavailable', reason='Not Implemented')


class SyncSuccessHandler(tornado.web.RequestHandler):
    def get(self):
        self.write('OK')


class SyncExceptionHandler(tornado.web.RequestHandler):
    def get(self):
        raise Exception('Ouch!')


class CustomDefaultHandler(tornado.web.ErrorHandler):
    """
    Default handler that is used in case of 404 error; in our tests
    it's used only if defined in the get_app() function.
    """
    pass


def make_app(settings={}):
    """
    Create a Tornado web application, useful to test
    different behaviors.
    """
    return tornado.web.Application([
        # custom handlers
        (r'/success/', SuccessHandler),
        (r'/nested/', NestedHandler),
        (r'/nested_wrap/', NestedWrapHandler),
        (r'/exception/', ExceptionHandler),
        (r'/http_exception/', HTTPExceptionHandler),
        # built-in handlers
        (r'/redirect/', tornado.web.RedirectHandler, {'url': '/success/'}),
        (r'/statics/(.*)', tornado.web.StaticFileHandler, {'path': STATIC_DIR}),
        # synchronous handlers
        (r'/sync_success/', SyncSuccessHandler),
        (r'/sync_exception/', SyncExceptionHandler),
    ], **settings)
