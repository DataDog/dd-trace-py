from contextlib import contextmanager
import json
import webtest

from nose.tools import eq_, assert_raises

from ddtrace import compat
from ddtrace.contrib.pyramid.patch import insert_tween_if_needed, patch, unpatch

from pyramid.httpexceptions import HTTPException

from .app import create_app

from tests.opentracer.utils import init_tracer
from ...test_tracer import get_dummy_tracer


class PyramidBase(object):
    """Base Pyramid test application"""
    instrument = False

    def setUp(self):
        self.tracer = get_dummy_tracer()
        self.create_app()

    def tearDown(self):
        unpatch()

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

    @contextmanager
    def override_instrument(self, new):
        old = self.instrument
        try:
            self.instrument = new
            yield
        finally:
            self.instrument = old



class PyramidTestCase(PyramidBase):
    """Pyramid TestCase that includes tests for automatic instrumentation"""

    def test_patch(self):
        # Make sure patch installs our instrumentation.
        with self.override_instrument(False):
            patch()
            self.create_app()

        res = self.app.get('/', status=200)
        assert b'idx' in res.body
        writer = self.tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)

    def test_override_instrument(self):
        old = self.instrument
        self.instrument = True
        with self.override_instrument(False):
            assert self.instrument is False
        assert self.instrument is True
        self.instrument = old

    def test_patch_unpatch(self):
        # Make sure unpatching removes our instrumentation.
        with self.override_instrument(False):
            patch()
            unpatch()
            self.create_app()

        res = self.app.get('/', status=200)
        assert b'idx' in res.body
        writer = self.tracer.writer
        spans = writer.pop()
        eq_(len(spans), 0)

    def test_patch_unpatch_patch(self):
        # Make sure we can unpatch and patch
        with self.override_instrument(False):
            patch()
            unpatch()
            patch()
            self.create_app()

        res = self.app.get('/', status=200)
        assert b'idx' in res.body
        writer = self.tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)

    def test_double_patch(self):
        # Make sure double patching only results in our instrumentation being
        # installed once.
        with self.override_instrument(False):
            patch()
            patch()
            self.create_app()

        res = self.app.get('/', status=200)
        assert b'idx' in res.body
        writer = self.tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)

    def test_double_unpatch(self):
        # Make sure double unpatching is a no-op
        with self.override_instrument(False):
            patch()
            unpatch()
            unpatch()
            patch()
            self.create_app()

        res = self.app.get('/', status=200)
        assert b'idx' in res.body
        writer = self.tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)

    def test_idempotence(self):
        # Ensure that patching is idempotent with manual instrumentation.
        patch()
        with self.override_instrument(False):
            # With self.instrument=True, self.create_app should create an app
            # that will also use the manual tracing method.
            # This, in addition to the patch() call above should mimic the app
            # being patched twice.
            self.create_app()

        # Perform a request, ensure no duplicate spans are created.
        res = self.app.get('/', status=200)
        assert b'idx' in res.body
        writer = self.tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)

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
        eq_(settings['pyramid.tweens'], 'ddtrace.contrib.pyramid:trace_tween_factory\npyramid.tweens.excview_tween_factory')

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

def includeme(config):
    pass


class TestPyramid(PyramidTestCase):
    instrument = True

    def get_settings(self):
        return {
            'datadog_trace_service': 'foobar',
        }

    def test_tween_overridden(self):
        # in case our tween is overriden by the user config we should
        # not log rendering
        self.override_settings({'pyramid.tweens': 'pyramid.tweens.excview_tween_factory'})
        self.app.get('/json', status=200)
        spans = self.tracer.writer.pop()
        eq_(len(spans), 0)


class TestPyramidDistributedTracing(PyramidBase):
    instrument = True

    def get_settings(self):
        return {
            'datadog_distributed_tracing': True,
        }

    def test_distributed_tracing(self):
        # ensure the Context is properly created
        # if distributed tracing is enabled
        headers = {
            'x-datadog-trace-id': '100',
            'x-datadog-parent-id': '42',
            'x-datadog-sampling-priority': '2',
        }
        self.app.get('/', headers=headers, status=200)
        writer = self.tracer.writer
        spans = writer.pop()
        eq_(len(spans), 1)
        # check the propagated Context
        span = spans[0]
        eq_(span.trace_id, 100)
        eq_(span.parent_id, 42)
        eq_(span.get_metric('_sampling_priority_v1'), 2)
