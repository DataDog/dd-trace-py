import os

from unittest import TestCase
from nose.tools import eq_, ok_, assert_raises

from routes import url_for
from paste import fixture
from paste.deploy import loadapp

from ddtrace.ext import http
from ddtrace.contrib.pylons import PylonsTraceMiddleware

from ...test_tracer import get_dummy_tracer


class PylonsTestCase(TestCase):
    """Pylons Test Controller that is used to test specific
    cases defined in the Pylons controller. To test a new behavior,
    add a new action in the `app.controllers.root` module.
    """
    conf_dir = os.path.dirname(os.path.abspath(__file__))

    def setUp(self):
        # initialize a real traced Pylons app
        self.tracer = get_dummy_tracer()
        wsgiapp = loadapp('config:test.ini', relative_to=PylonsTestCase.conf_dir)
        app = PylonsTraceMiddleware(wsgiapp, self.tracer, service='web')
        self.app = fixture.TestApp(app)

    def test_success_200(self):
        res = self.app.get(url_for(controller='root', action='index'))
        eq_(res.status, 200)

        spans = self.tracer.writer.pop()
        ok_(spans, spans)
        eq_(len(spans), 1)
        span = spans[0]

        eq_(span.service, 'web')
        eq_(span.resource, 'root.index')
        eq_(span.meta.get(http.STATUS_CODE), '200')
        eq_(span.error, 0)

    def test_failure_500(self):
        with assert_raises(Exception):
            self.app.get(url_for(controller='root', action='raise_exception'))

        spans = self.tracer.writer.pop()
        ok_(spans, spans)
        eq_(len(spans), 1)
        span = spans[0]

        eq_(span.service, 'web')
        eq_(span.resource, 'root.raise_exception')
        eq_(span.error, 1)
        eq_(span.get_tag('http.status_code'), '500')
        eq_(span.get_tag('error.msg'), 'Ouch!')
        ok_('Exception: Ouch!' in span.get_tag('error.stack'))

    def test_failure_500_with_wrong_code(self):
        with assert_raises(Exception):
            self.app.get(url_for(controller='root', action='raise_wrong_code'))

        spans = self.tracer.writer.pop()
        ok_(spans, spans)
        eq_(len(spans), 1)
        span = spans[0]

        eq_(span.service, 'web')
        eq_(span.resource, 'root.raise_wrong_code')
        eq_(span.error, 1)
        eq_(span.get_tag('http.status_code'), '500')
        eq_(span.get_tag('error.msg'), 'Ouch!')
        ok_('Exception: Ouch!' in span.get_tag('error.stack'))

    def test_failure_500_with_custom_code(self):
        with assert_raises(Exception):
            self.app.get(url_for(controller='root', action='raise_custom_code'))

        spans = self.tracer.writer.pop()
        ok_(spans, spans)
        eq_(len(spans), 1)
        span = spans[0]

        eq_(span.service, 'web')
        eq_(span.resource, 'root.raise_custom_code')
        eq_(span.error, 1)
        eq_(span.get_tag('http.status_code'), '512')
        eq_(span.get_tag('error.msg'), 'Ouch!')
        ok_('Exception: Ouch!' in span.get_tag('error.stack'))

    def test_failure_500_with_code_method(self):
        with assert_raises(Exception):
            self.app.get(url_for(controller='root', action='raise_code_method'))

        spans = self.tracer.writer.pop()
        ok_(spans, spans)
        eq_(len(spans), 1)
        span = spans[0]

        eq_(span.service, 'web')
        eq_(span.resource, 'root.raise_code_method')
        eq_(span.error, 1)
        eq_(span.get_tag('http.status_code'), '500')
        eq_(span.get_tag('error.msg'), 'Ouch!')
