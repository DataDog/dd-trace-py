import bottle
import ddtrace
import webtest

from unittest import TestCase
from nose.tools import eq_, ok_
from tests.test_tracer import get_dummy_tracer

from ddtrace import compat
from ddtrace.contrib.bottle import TracePlugin


SERVICE = 'bottle-app'


class TraceBottleTest(TestCase):
    """
    Ensures that Bottle is properly traced.
    """
    def setUp(self):
        # provide a dummy tracer
        self.tracer = get_dummy_tracer()
        self._original_tracer = ddtrace.tracer
        ddtrace.tracer = self.tracer
        # provide a Bottle app
        self.app = bottle.Bottle()

    def tearDown(self):
        # restore the tracer
        ddtrace.tracer = self._original_tracer

    def _trace_app(self, tracer=None):
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
