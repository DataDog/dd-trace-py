
# stdlib
import logging
import sys
from wsgiref.simple_server import make_server

# 3p
from pyramid.config import Configurator
from pyramid.view import view_config
from pyramid.httpexceptions import HTTPInternalServerError
import webtest
from nose.tools import eq_

# project
import ddtrace

def test_200():
    app, tracer = _get_test_app(service='foobar')
    res = app.get('/', status=200)
    assert b'idx' in res.body

    writer = tracer.writer
    spans = writer.pop()
    eq_(len(spans), 1)
    s = spans[0]
    eq_(s.service, 'foobar')
    eq_(s.resource, 'index')
    eq_(s.error, 0)
    eq_(s.span_type, 'http')
    eq_(s.meta.get('http.status_code'), '200')
    eq_(s.meta.get('http.url'), '/')

    # ensure services are set correcgly
    services = writer.pop_services()
    expected = {
        'foobar': {"app":"pyramid", "app_type":"web"}
    }
    eq_(services, expected)


def test_404():
    app, tracer = _get_test_app(service='foobar')
    res = app.get('/404', status=404)

    writer = tracer.writer
    spans = writer.pop()
    eq_(len(spans), 1)
    s = spans[0]
    eq_(s.service, 'foobar')
    eq_(s.resource, '404')
    eq_(s.error, 0)
    eq_(s.span_type, 'http')
    eq_(s.meta.get('http.status_code'), '404')
    eq_(s.meta.get('http.url'), '/404')


def test_exception():
    app, tracer = _get_test_app(service='foobar')
    try:
        app.get('/exception', status=500)
    except ZeroDivisionError:
        pass

    writer = tracer.writer
    spans = writer.pop()
    eq_(len(spans), 1)
    s = spans[0]
    eq_(s.service, 'foobar')
    eq_(s.resource, 'exception')
    eq_(s.error, 1)
    eq_(s.span_type, 'http')
    eq_(s.meta.get('http.status_code'), '500')
    eq_(s.meta.get('http.url'), '/exception')

def test_500():
    app, tracer = _get_test_app(service='foobar')
    app.get('/error', status=500)

    writer = tracer.writer
    spans = writer.pop()
    eq_(len(spans), 1)
    s = spans[0]
    eq_(s.service, 'foobar')
    eq_(s.resource, 'error')
    eq_(s.error, 1)
    eq_(s.span_type, 'http')
    eq_(s.meta.get('http.status_code'), '500')
    eq_(s.meta.get('http.url'), '/error')


def _get_app(service=None, tracer=None):
    """ return a pyramid wsgi app with various urls. """

    def index(request):
        return 'idx'

    def error(request):
        raise HTTPInternalServerError("oh no")

    def exception(request):
        1/0

    settings = {
        'datadog_trace_service': service,
        'datadog_tracer': tracer or ddtrace.tracer
    }

    config = Configurator(settings=settings)
    config.add_tween('ddtrace.contrib.pyramid:trace_tween_factory')
    config.add_route('index', '/')
    config.add_route('error', '/error')
    config.add_route('exception', '/exception')
    config.add_view(index, route_name='index', renderer='string')
    config.add_view(error, route_name='error', renderer='string')
    config.add_view(exception, route_name='exception', renderer='string')
    return config.make_wsgi_app()


def _get_test_app(service=None):
    """ return a webtest'able version of our test app. """
    from tests.test_tracer import get_dummy_tracer
    tracer = get_dummy_tracer()
    app = _get_app(service=service, tracer=tracer)
    return webtest.TestApp(app), tracer


if __name__ == '__main__':
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
    ddtrace.tracer.debug_logging = True
    app = _get_app()
    port = 8080
    server = make_server('0.0.0.0', port, app)
    print('running on %s' % port)
    server.serve_forever()
