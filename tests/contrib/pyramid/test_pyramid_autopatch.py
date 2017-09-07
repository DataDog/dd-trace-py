# stdlib
import json
import logging
import sys
import webtest
from nose.tools import eq_
from pyramid.config import Configurator
from pyramid.httpexceptions import HTTPInternalServerError

# 3p
from pyramid.response import Response
from pyramid.view import view_config
from wsgiref.simple_server import make_server

# project
import ddtrace
from ddtrace import compat

def _include_me(config):
    pass

def test_config_include():
    """ This test makes sure that relative imports still work when the
    application is run with ddtrace-run """
    config = Configurator()
    config.include('._include_me')

def test_200():
    app, tracer = _get_test_app(service='foobar')
    res = app.get('/', status=200)
    assert b'idx' in res.body

    writer = tracer.writer
    spans = writer.pop()
    eq_(len(spans), 1)
    s = spans[0]
    eq_(s.service, 'foobar')
    eq_(s.resource, 'GET index')
    eq_(s.error, 0)
    eq_(s.span_type, 'http')
    eq_(s.meta.get('http.method'), 'GET')
    eq_(s.meta.get('http.status_code'), '200')
    eq_(s.meta.get('http.url'), '/')

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
    eq_(s.resource, 'GET exception')
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
    eq_(s.resource, 'GET error')
    eq_(s.error, 1)
    eq_(s.span_type, 'http')
    eq_(s.meta.get('http.method'), 'GET')
    eq_(s.meta.get('http.status_code'), '500')
    eq_(s.meta.get('http.url'), '/error')
    assert type(s.error) == int


def test_json():
    app, tracer = _get_test_app(service='foobar')
    res = app.get('/json', status=200)
    parsed = json.loads(compat.to_unicode(res.body))
    eq_(parsed, {'a': 1})

    writer = tracer.writer
    spans = writer.pop()
    eq_(len(spans), 2)
    spans_by_name = {s.name: s for s in spans}
    s = spans_by_name['pyramid.request']
    eq_(s.service, 'foobar')
    eq_(s.resource, 'GET json')
    eq_(s.error, 0)
    eq_(s.span_type, 'http')
    eq_(s.meta.get('http.method'), 'GET')
    eq_(s.meta.get('http.status_code'), '200')
    eq_(s.meta.get('http.url'), '/json')

    s = spans_by_name['pyramid.render']
    eq_(s.service, 'foobar')
    eq_(s.error, 0)
    eq_(s.span_type, 'template')

def includeme(config):
    pass

def test_include():
    """ Test that includes do not create conflicts """
    from ...test_tracer import get_dummy_tracer
    from ...util import override_global_tracer
    tracer = get_dummy_tracer()
    with override_global_tracer(tracer):
        config = Configurator(settings={'pyramid.includes': 'tests.contrib.pyramid.test_pyramid_autopatch'})
        app = webtest.TestApp(config.make_wsgi_app())
        app.get('/', status=404)
        spans = tracer.writer.pop()
        assert spans
        eq_(len(spans), 1)

def _get_app(service=None, tracer=None):
    """ return a pyramid wsgi app with various urls. """

    def index(request):
        return Response('idx')

    def error(request):
        raise HTTPInternalServerError("oh no")

    def exception(request):
        1 / 0

    def json(request):
        return {'a': 1}

    config = Configurator()
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
    from tests.test_tracer import DummyWriter
    ddtrace.tracer.writer = DummyWriter()

    app = _get_app(service=service, tracer=ddtrace.tracer)
    return webtest.TestApp(app), ddtrace.tracer


if __name__ == '__main__':
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
    ddtrace.tracer.debug_logging = True
    app = _get_app()
    port = 8080
    server = make_server('0.0.0.0', port, app)
    print('running on %s' % port)
    server.serve_forever()
