# stdlib
import logging
import json
import sys
from wsgiref.simple_server import make_server

# 3p
from pyramid.response import Response
from pyramid.config import Configurator
from pyramid.httpexceptions import HTTPInternalServerError
import webtest
from nose.tools import eq_

# project
import ddtrace
from ddtrace import compat
from ddtrace.contrib.pyramid import trace_pyramid
from ddtrace.contrib.pyramid.patch import insert_tween_if_needed

class PyramidBase(object):

    def test_200(self):
        res = self.app.get('/', status=200)
        assert b'idx' in res.body

        writer = self.tracer.writer
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
        eq_(s.meta.get('pyramid.route.name'), 'index')

        # ensure services are set correctly
        services = writer.pop_services()
        expected = {
            'foobar': {"app": "pyramid", "app_type": "web"}
        }
        eq_(services, expected)

    def test_404(self):
        self.app.get('/404', status=404)

        writer = self.tracer.writer
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

    def test_exception(self):
        try:
            self.app.get('/exception', status=500)
        except ZeroDivisionError:
            pass

        writer = self.tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.service, 'foobar')
        eq_(s.resource, 'GET exception')
        eq_(s.error, 1)
        eq_(s.span_type, 'http')
        eq_(s.meta.get('http.method'), 'GET')
        eq_(s.meta.get('http.status_code'), '500')
        eq_(s.meta.get('http.url'), '/exception')
        eq_(s.meta.get('pyramid.route.name'), 'exception')

    def test_500(self):
        self.app.get('/error', status=500)

        writer = self.tracer.writer
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
        eq_(s.meta.get('pyramid.route.name'), 'error')
        assert type(s.error) == int

    def test_json(self):
        res = self.app.get('/json', status=200)
        parsed = json.loads(compat.to_unicode(res.body))
        eq_(parsed, {'a': 1})

        writer = self.tracer.writer
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
        eq_(s.meta.get('pyramid.route.name'), 'json')

        s = spans_by_name['pyramid.render']
        eq_(s.service, 'foobar')
        eq_(s.error, 0)
        eq_(s.span_type, 'template')

class TestPyramid(PyramidBase):
    def setUp(self):
        from tests.test_tracer import get_dummy_tracer
        self.tracer = get_dummy_tracer()

        settings = {
            'datadog_trace_service': 'foobar',
            'datadog_tracer': self.tracer
        }
        config = Configurator(settings=settings)
        trace_pyramid(config)

        app = get_app(config)
        self.app = webtest.TestApp(app)

def includeme(config):
    pass

def test_include_conflicts():
    """ Test that includes do not create conflicts """
    from ...test_tracer import get_dummy_tracer
    from ...util import override_global_tracer
    tracer = get_dummy_tracer()
    with override_global_tracer(tracer):
        config = Configurator(settings={'pyramid.includes': 'tests.contrib.pyramid.test_pyramid'})
        trace_pyramid(config)
        app = webtest.TestApp(config.make_wsgi_app())
        app.get('/', status=404)
        spans = tracer.writer.pop()
        assert spans
        eq_(len(spans), 1)

def test_tween_overriden():
    """ In case our tween is overriden by the user config we should not log
    rendering """
    from ...test_tracer import get_dummy_tracer
    from ...util import override_global_tracer
    tracer = get_dummy_tracer()
    with override_global_tracer(tracer):
        config = Configurator(settings={'pyramid.tweens': 'pyramid.tweens.excview_tween_factory'})
        trace_pyramid(config)

        def json(request):
            return {'a': 1}
        config.add_route('json', '/json')
        config.add_view(json, route_name='json', renderer='json')
        app = webtest.TestApp(config.make_wsgi_app())
        app.get('/json', status=200)
        spans = tracer.writer.pop()
        assert not spans

def test_insert_tween_if_needed_already_set():
    settings = {'pyramid.tweens': 'ddtrace.contrib.pyramid:trace_tween_factory'}
    insert_tween_if_needed(settings)
    eq_(settings['pyramid.tweens'], 'ddtrace.contrib.pyramid:trace_tween_factory')

def test_insert_tween_if_needed_none():
    settings = {'pyramid.tweens': ''}
    insert_tween_if_needed(settings)
    eq_(settings['pyramid.tweens'], '')

def test_insert_tween_if_needed_excview():
    settings = {'pyramid.tweens': 'pyramid.tweens.excview_tween_factory'}
    insert_tween_if_needed(settings)
    eq_(settings['pyramid.tweens'], 'ddtrace.contrib.pyramid:trace_tween_factory\npyramid.tweens.excview_tween_factory')

def test_insert_tween_if_needed_excview_and_other():
    settings = {'pyramid.tweens': 'a.first.tween\npyramid.tweens.excview_tween_factory\na.last.tween\n'}
    insert_tween_if_needed(settings)
    eq_(settings['pyramid.tweens'],
        'a.first.tween\n'
        'ddtrace.contrib.pyramid:trace_tween_factory\n'
        'pyramid.tweens.excview_tween_factory\n'
        'a.last.tween\n')

def test_insert_tween_if_needed_others():
    settings = {'pyramid.tweens': 'a.random.tween\nand.another.one'}
    insert_tween_if_needed(settings)
    eq_(settings['pyramid.tweens'], 'a.random.tween\nand.another.one\nddtrace.contrib.pyramid:trace_tween_factory')

def get_app(config):
    """ return a pyramid wsgi app with various urls. """

    def index(request):
        return Response('idx')

    def error(request):
        raise HTTPInternalServerError("oh no")

    def exception(request):
        1 / 0

    def json(request):
        return {'a': 1}

    config.add_route('index', '/')
    config.add_route('error', '/error')
    config.add_route('exception', '/exception')
    config.add_route('json', '/json')
    config.add_view(index, route_name='index')
    config.add_view(error, route_name='error')
    config.add_view(exception, route_name='exception')
    config.add_view(json, route_name='json', renderer='json')
    return config.make_wsgi_app()


if __name__ == '__main__':
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
    ddtrace.tracer.debug_logging = True
    settings = {
        'datadog_trace_service': 'foobar',
        'datadog_tracer': ddtrace.tracer
    }
    config = Configurator(settings=settings)
    trace_pyramid(config)
    app = get_app(config)
    port = 8080
    server = make_server('0.0.0.0', port, app)
    print('running on %s' % port)
    server.serve_forever()
