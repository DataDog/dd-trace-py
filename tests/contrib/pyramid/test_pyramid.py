# stdlib
import logging
import json
import sys
from wsgiref.simple_server import make_server

# 3p
from pyramid.response import Response
from pyramid.config import Configurator
from pyramid.renderers import render_to_response
from pyramid.httpexceptions import (
    HTTPInternalServerError,
    HTTPFound,
    HTTPNotFound,
    HTTPException,
    HTTPNoContent,
)
import webtest
from nose.tools import eq_, assert_raises

# project
import ddtrace
from ddtrace import compat
from ddtrace.contrib.pyramid import trace_pyramid
from ddtrace.contrib.pyramid.patch import insert_tween_if_needed

from ...test_tracer import get_dummy_tracer
from ...util import override_global_tracer


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

    def test_302(self):
        self.app.get('/redirect', status=302)

        writer = self.tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.service, 'foobar')
        eq_(s.resource, 'GET raise_redirect')
        eq_(s.error, 0)
        eq_(s.span_type, 'http')
        eq_(s.meta.get('http.method'), 'GET')
        eq_(s.meta.get('http.status_code'), '302')
        eq_(s.meta.get('http.url'), '/redirect')

    def test_204(self):
        self.app.get('/nocontent', status=204)

        writer = self.tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.service, 'foobar')
        eq_(s.resource, 'GET raise_no_content')
        eq_(s.error, 0)
        eq_(s.span_type, 'http')
        eq_(s.meta.get('http.method'), 'GET')
        eq_(s.meta.get('http.status_code'), '204')
        eq_(s.meta.get('http.url'), '/nocontent')

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

    def test_renderer(self):
        res = self.app.get('/renderer', status=200)
        assert self.rend._received['request'] is not None
        self.rend.assert_(foo='bar')
        writer = self.tracer.writer
        spans = writer.pop()
        eq_(len(spans), 2)
        spans_by_name = {s.name: s for s in spans}
        s = spans_by_name['pyramid.request']
        eq_(s.service, 'foobar')
        eq_(s.resource, 'GET renderer')
        eq_(s.error, 0)
        eq_(s.span_type, 'http')
        eq_(s.meta.get('http.method'), 'GET')
        eq_(s.meta.get('http.status_code'), '200')
        eq_(s.meta.get('http.url'), '/renderer')
        eq_(s.meta.get('pyramid.route.name'), 'renderer')

        s = spans_by_name['pyramid.render']
        eq_(s.service, 'foobar')
        eq_(s.error, 0)
        eq_(s.span_type, 'template')

    def test_http_exception_response(self):
        with assert_raises(HTTPException):
            self.app.get('/404/raise_exception', status=404)

        writer = self.tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.service, 'foobar')
        eq_(s.resource, '404')
        eq_(s.error, 1)
        eq_(s.span_type, 'http')
        eq_(s.meta.get('http.method'), 'GET')
        eq_(s.meta.get('http.status_code'), '404')
        eq_(s.meta.get('http.url'), '/404/raise_exception')


def includeme(config):
    pass

def test_include_conflicts():
    """ Test that includes do not create conflicts """
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

    def renderer(request):
        return render_to_response('template.pt', {'foo': 'bar'}, request=request)

    def raise_redirect(request):
        raise HTTPFound()

    def raise_no_content(request):
        raise HTTPNoContent()

    config.add_route('index', '/')
    config.add_route('error', '/error')
    config.add_route('exception', '/exception')
    config.add_route('json', '/json')
    config.add_route('renderer', '/renderer')
    config.add_route('raise_redirect', '/redirect')
    config.add_route('raise_no_content', '/nocontent')
    config.add_view(index, route_name='index')
    config.add_view(error, route_name='error')
    config.add_view(exception, route_name='exception')
    config.add_view(json, route_name='json', renderer='json')
    config.add_view(renderer, route_name='renderer', renderer='template.pt')
    config.add_view(raise_redirect, route_name='raise_redirect')
    config.add_view(raise_no_content, route_name='raise_no_content')
    return config.make_wsgi_app()


def custom_exception_view(context, request):
    """Custom view that forces a HTTPException when no views
    are found to handle given request
    """
    if 'raise_exception' in request.url:
        raise HTTPNotFound()
    else:
        return HTTPNotFound()


class TestPyramid(PyramidBase):
    def setUp(self):
        self.tracer = get_dummy_tracer()
        settings = {
            'datadog_trace_service': 'foobar',
            'datadog_tracer': self.tracer,
        }

        config = Configurator(settings=settings)
        self.rend = config.testing_add_renderer('template.pt')
        # required to reproduce a regression test
        config.add_notfound_view(custom_exception_view)
        trace_pyramid(config)

        app = get_app(config)
        self.app = webtest.TestApp(app)
