import os

from unittest import TestCase
from nose.tools import eq_, ok_, assert_raises

from routes import url_for
from paste import fixture
from paste.deploy import loadapp

from ddtrace.ext import http
from ddtrace.constants import SAMPLING_PRIORITY_KEY
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

    def test_template_render(self):
        res = self.app.get(url_for(controller='root', action='render'))
        eq_(res.status, 200)

        spans = self.tracer.writer.pop()
        ok_(spans, spans)
        eq_(len(spans), 2)
        request = spans[0]
        template = spans[1]

        eq_(request.service, 'web')
        eq_(request.resource, 'root.render')
        eq_(request.meta.get(http.STATUS_CODE), '200')
        eq_(request.error, 0)

        eq_(template.service, 'web')
        eq_(template.resource, 'pylons.render')
        eq_(template.meta.get('template.name'), '/template.mako')
        eq_(template.error, 0)

    def test_template_render_exception(self):
        with assert_raises(Exception):
            self.app.get(url_for(controller='root', action='render_exception'))

        spans = self.tracer.writer.pop()
        ok_(spans, spans)
        eq_(len(spans), 2)
        request = spans[0]
        template = spans[1]

        eq_(request.service, 'web')
        eq_(request.resource, 'root.render_exception')
        eq_(request.meta.get(http.STATUS_CODE), '500')
        eq_(request.error, 1)

        eq_(template.service, 'web')
        eq_(template.resource, 'pylons.render')
        eq_(template.meta.get('template.name'), '/exception.mako')
        eq_(template.error, 1)
        eq_(template.get_tag('error.msg'), 'integer division or modulo by zero')
        ok_('ZeroDivisionError: integer division or modulo by zero' in template.get_tag('error.stack'))

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

    def test_distributed_tracing_default(self):
        # ensure by default, distributed tracing is not enabled
        headers = {
            'x-datadog-trace-id': '100',
            'x-datadog-parent-id': '42',
            'x-datadog-sampling-priority': '2',
        }
        res = self.app.get(url_for(controller='root', action='index'), headers=headers)
        eq_(res.status, 200)

        spans = self.tracer.writer.pop()
        ok_(spans, spans)
        eq_(len(spans), 1)
        span = spans[0]

        ok_(span.trace_id != 100)
        ok_(span.parent_id != 42)
        ok_(span.get_metric(SAMPLING_PRIORITY_KEY) is None)

    def test_distributed_tracing_enabled(self):
        # ensure distributed tracing propagator is working
        middleware = self.app.app
        middleware._distributed_tracing = True
        headers = {
            'x-datadog-trace-id': '100',
            'x-datadog-parent-id': '42',
            'x-datadog-sampling-priority': '2',
        }

        res = self.app.get(url_for(controller='root', action='index'), headers=headers)
        eq_(res.status, 200)

        spans = self.tracer.writer.pop()
        ok_(spans, spans)
        eq_(len(spans), 1)
        span = spans[0]

        eq_(span.trace_id, 100)
        eq_(span.parent_id, 42)
        eq_(span.get_metric(SAMPLING_PRIORITY_KEY), 2)
