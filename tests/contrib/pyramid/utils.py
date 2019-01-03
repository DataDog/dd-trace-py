import json

from nose.tools import eq_, assert_raises
from pyramid.httpexceptions import HTTPException
import webtest

from ddtrace import compat
from ddtrace.constants import EVENT_SAMPLE_RATE_KEY
from ddtrace.contrib.pyramid.patch import insert_tween_if_needed

from .app import create_app

from ...opentracer.utils import init_tracer
from ...base import BaseTracerTestCase


class PyramidBase(BaseTracerTestCase):
    """Base Pyramid test application"""
    def setUp(self):
        super(PyramidBase, self).setUp()
        self.create_app()

    def create_app(self, settings=None):
        # get default settings or use what is provided
        settings = settings or self.get_settings()
        # always set the dummy tracer as a default tracer
        settings.update({'datadog_tracer': self.tracer})

        app, renderer = create_app(settings, self.instrument)
        self.app = webtest.TestApp(app)
        self.renderer = renderer

    def get_settings(self):
        return {}

    def override_settings(self, settings):
        self.create_app(settings)


class PyramidTestCase(PyramidBase):
    """Pyramid TestCase that includes tests for automatic instrumentation"""
    instrument = True

    def get_settings(self):
        return {
            'datadog_trace_service': 'foobar',
        }

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

    def test_event_sample_rate(self):
        with self.override_config('pyramid', dict(event_sample_rate=1)):
            res = self.app.get('/', status=200)
            assert b'idx' in res.body

            self.assert_structure(
                dict(name='pyramid.request', metrics={EVENT_SAMPLE_RATE_KEY: 1}),
            )

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
        self.app.get('/renderer', status=200)
        assert self.renderer._received['request'] is not None

        self.renderer.assert_(foo='bar')
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

    def test_insert_tween_if_needed_already_set(self):
        settings = {'pyramid.tweens': 'ddtrace.contrib.pyramid:trace_tween_factory'}
        insert_tween_if_needed(settings)
        eq_(settings['pyramid.tweens'], 'ddtrace.contrib.pyramid:trace_tween_factory')

    def test_insert_tween_if_needed_none(self):
        settings = {'pyramid.tweens': ''}
        insert_tween_if_needed(settings)
        eq_(settings['pyramid.tweens'], '')

    def test_insert_tween_if_needed_excview(self):
        settings = {'pyramid.tweens': 'pyramid.tweens.excview_tween_factory'}
        insert_tween_if_needed(settings)
        eq_(
            settings['pyramid.tweens'],
            'ddtrace.contrib.pyramid:trace_tween_factory\npyramid.tweens.excview_tween_factory',
        )

    def test_insert_tween_if_needed_excview_and_other(self):
        settings = {'pyramid.tweens': 'a.first.tween\npyramid.tweens.excview_tween_factory\na.last.tween\n'}
        insert_tween_if_needed(settings)
        eq_(settings['pyramid.tweens'],
            'a.first.tween\n'
            'ddtrace.contrib.pyramid:trace_tween_factory\n'
            'pyramid.tweens.excview_tween_factory\n'
            'a.last.tween\n')

    def test_insert_tween_if_needed_others(self):
        settings = {'pyramid.tweens': 'a.random.tween\nand.another.one'}
        insert_tween_if_needed(settings)
        eq_(settings['pyramid.tweens'], 'a.random.tween\nand.another.one\nddtrace.contrib.pyramid:trace_tween_factory')

    def test_include_conflicts(self):
        # test that includes do not create conflicts
        self.override_settings({'pyramid.includes': 'tests.contrib.pyramid.test_pyramid'})
        self.app.get('/404', status=404)
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)

    def test_200_ot(self):
        """OpenTracing version of test_200."""
        ot_tracer = init_tracer('pyramid_svc', self.tracer)

        with ot_tracer.start_active_span('pyramid_get'):
            res = self.app.get('/', status=200)
            assert b'idx' in res.body

        writer = self.tracer.writer
        spans = writer.pop()
        eq_(len(spans), 2)

        ot_span, dd_span = spans

        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(ot_span.name, 'pyramid_get')
        eq_(ot_span.service, 'pyramid_svc')

        eq_(dd_span.service, 'foobar')
        eq_(dd_span.resource, 'GET index')
        eq_(dd_span.error, 0)
        eq_(dd_span.span_type, 'http')
        eq_(dd_span.meta.get('http.method'), 'GET')
        eq_(dd_span.meta.get('http.status_code'), '200')
        eq_(dd_span.meta.get('http.url'), '/')
        eq_(dd_span.meta.get('pyramid.route.name'), 'index')
