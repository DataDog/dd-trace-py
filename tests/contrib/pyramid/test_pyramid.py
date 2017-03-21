
# stdlib
import logging
import json
import sys
from wsgiref.simple_server import make_server

# 3p
from pyramid.response import Response
from pyramid.config import Configurator
from pyramid.view import view_config
from pyramid.httpexceptions import HTTPInternalServerError
import webtest
from nose.tools import eq_

# project
import ddtrace
from ddtrace import compat
from ddtrace.contrib.pyramid import trace_pyramid


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
    eq_(s.meta.get('http.method'), 'GET')
    eq_(s.meta.get('http.status_code'), '200')
    eq_(s.meta.get('http.url'), '/')
    eq_(s.meta.get('pyramid.route.name'), 'index')

    # ensure services are set correctly
    services = writer.pop_services()
    expected = {
        'foobar': {"app": "pyramid", "app_type": "web"}
    }
    eq_(services, expected)


def test_404():
    app, tracer = _get_test_app(service='foobar')
    app.get('/404', status=404)

    writer = tracer.writer
    spans = writer.pop()
    eq_(len(spans), 1)
    s = spans[0]
    eq_(s.service, 'foobar')
    eq_(s.resource, '404')
    eq_(s.error, 0)
    eq_(s.span_type, 'http')
    eq_(s.meta.get('http.method'), 'GET')
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
    eq_(s.meta.get('http.method'), 'GET')
    eq_(s.meta.get('http.status_code'), '500')
    eq_(s.meta.get('http.url'), '/exception')
    eq_(s.meta.get('pyramid.route.name'), 'exception')


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
    eq_(s.meta.get('http.method'), 'GET')
    eq_(s.meta.get('http.status_code'), '500')
    eq_(s.meta.get('http.url'), '/error')
    eq_(s.meta.get('pyramid.route.name'), 'error')
    assert type(s.error) == int


def test_json():
    app, tracer = _get_test_app(service='foobar')
    res = app.get('/json', status=200)
    parsed = json.loads(compat.to_unicode(res.body))
    eq_(parsed, {'a':1})

    writer = tracer.writer
    spans = writer.pop()
    eq_(len(spans), 2)
    spans_by_name = {s.name:s for s in spans}
    s = spans_by_name['pyramid.request']
    eq_(s.service, 'foobar')
    eq_(s.resource, 'json')
    eq_(s.error, 0)
    eq_(s.span_type, 'http')
    eq_(s.meta.get('http.method'), 'GET')
    eq_(s.meta.get('http.status_code'), '200')
    eq_(s.meta.get('http.url'), '/json')
    eq_(s.meta.get('pyramid.route.name'), 'json')

    s = spans_by_name['pyramid.render']
    eq_(s.service, 'foobar')
    eq_(s.error, 0)
    eq_(s.span_type, 'template')


def _get_app(service=None, tracer=None):
    """ return a pyramid wsgi app with various urls. """

    def index(request):
        return Response('idx')

    def error(request):
        raise HTTPInternalServerError("oh no")

    def exception(request):
        1/0

    def json(request):
        return {'a':1}

    settings = {
        'datadog_trace_service': service,
        'datadog_tracer': tracer or ddtrace.tracer
    }

    config = Configurator(settings=settings)
    trace_pyramid(config)
    config.add_route('index', '/')
    config.add_route('error', '/error')
    config.add_route('exception', '/exception')
    config.add_route('json', '/json')
    config.add_view(index, route_name='index')
    config.add_view(error, route_name='error')
    config.add_view(exception, route_name='exception')
    config.add_view(json, route_name='json', renderer='json')
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
