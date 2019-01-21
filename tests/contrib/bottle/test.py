import bottle
import ddtrace
import webtest

from nose.tools import eq_, ok_
from tests.opentracer.utils import init_tracer
from ...base import BaseTracerTestCase

from ddtrace import compat
from ddtrace.constants import EVENT_SAMPLE_RATE_KEY
from ddtrace.contrib.bottle import TracePlugin

SERVICE = 'bottle-app'


class TraceBottleTest(BaseTracerTestCase):
    """
    Ensures that Bottle is properly traced.
    """
    def setUp(self):
        super(TraceBottleTest, self).setUp()

        # provide a dummy tracer
        self._original_tracer = ddtrace.tracer
        ddtrace.tracer = self.tracer
        # provide a Bottle app
        self.app = bottle.Bottle()

    def tearDown(self):
        # restore the tracer
        ddtrace.tracer = self._original_tracer

    def _trace_app(self, tracer=None):
        self.app.install(TracePlugin(service=SERVICE, tracer=tracer))
        self.app = webtest.TestApp(self.app)

    def test_200(self):
        # setup our test app
        @self.app.route('/hi/<name>')
        def hi(name):
            return 'hi %s' % name
        self._trace_app(self.tracer)

        # make a request
        resp = self.app.get('/hi/dougie')
        eq_(resp.status_int, 200)
        eq_(compat.to_unicode(resp.body), u'hi dougie')
        # validate it's traced
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.name, 'bottle.request')
        eq_(s.service, 'bottle-app')
        eq_(s.span_type, 'web')
        eq_(s.resource, 'GET /hi/<name>')
        eq_(s.get_tag('http.status_code'), '200')
        eq_(s.get_tag('http.method'), 'GET')

        services = self.tracer.writer.pop_services()
        eq_(len(services), 1)
        ok_(SERVICE in services)
        s = services[SERVICE]
        eq_(s['app_type'], 'web')
        eq_(s['app'], 'bottle')

    def test_500(self):
        @self.app.route('/hi')
        def hi():
            raise Exception('oh no')
        self._trace_app(self.tracer)

        # make a request
        try:
            resp = self.app.get('/hi')
            eq_(resp.status_int, 500)
        except Exception:
            pass

        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.name, 'bottle.request')
        eq_(s.service, 'bottle-app')
        eq_(s.resource, 'GET /hi')
        eq_(s.get_tag('http.status_code'), '500')
        eq_(s.get_tag('http.method'), 'GET')

    def test_bottle_global_tracer(self):
        # without providing a Tracer instance, it should work
        @self.app.route('/home/')
        def home():
            return 'Hello world'
        self._trace_app()

        # make a request
        resp = self.app.get('/home/')
        eq_(resp.status_int, 200)
        # validate it's traced
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.name, 'bottle.request')
        eq_(s.service, 'bottle-app')
        eq_(s.resource, 'GET /home/')
        eq_(s.get_tag('http.status_code'), '200')
        eq_(s.get_tag('http.method'), 'GET')

    def test_event_sample_rate(self):
        # setup our test app
        @self.app.route('/hi/<name>')
        def hi(name):
            return 'hi %s' % name
        self._trace_app(self.tracer)

        # make a request
        with self.override_config('bottle', dict(event_sample_rate=1)):
            resp = self.app.get('/hi/dougie')
            eq_(resp.status_int, 200)
            eq_(compat.to_unicode(resp.body), u'hi dougie')

        root = self.get_root_span()
        root.assert_matches(
            name='bottle.request',
            metrics={
                EVENT_SAMPLE_RATE_KEY: 1,
            },
        )

        for span in self.spans:
            if span == root:
                continue
            self.assertIsNone(span.get_metric(EVENT_SAMPLE_RATE_KEY))

    def test_200_ot(self):
        ot_tracer = init_tracer('my_svc', self.tracer)

        # setup our test app
        @self.app.route('/hi/<name>')
        def hi(name):
            return 'hi %s' % name
        self._trace_app(self.tracer)

        # make a request
        with ot_tracer.start_active_span('ot_span'):
            resp = self.app.get('/hi/dougie')

        eq_(resp.status_int, 200)
        eq_(compat.to_unicode(resp.body), u'hi dougie')
        # validate it's traced
        spans = self.tracer.writer.pop()
        eq_(len(spans), 2)
        ot_span, dd_span = spans

        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(ot_span.resource, 'ot_span')

        eq_(dd_span.name, 'bottle.request')
        eq_(dd_span.service, 'bottle-app')
        eq_(dd_span.resource, 'GET /hi/<name>')
        eq_(dd_span.get_tag('http.status_code'), '200')
        eq_(dd_span.get_tag('http.method'), 'GET')

        services = self.tracer.writer.pop_services()
        eq_(len(services), 1)
        ok_(SERVICE in services)
        s = services[SERVICE]
        eq_(s['app_type'], 'web')
        eq_(s['app'], 'bottle')
