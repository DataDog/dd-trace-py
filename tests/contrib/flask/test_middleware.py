# -*- coding: utf-8 -*-
import time
import re

from nose.tools import eq_, ok_
from unittest import TestCase

from ddtrace.contrib.flask import TraceMiddleware
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.ext import http, errors

from tests.opentracer.utils import init_tracer
from .web import create_app
from ...test_tracer import get_dummy_tracer


class TestFlask(TestCase):
    """Ensures Flask is properly instrumented."""

    def setUp(self):
        self.tracer = get_dummy_tracer()
        self.flask_app = create_app()
        self.traced_app = TraceMiddleware(
            self.flask_app,
            self.tracer,
            service='test.flask.service',
            distributed_tracing=True,
        )

        # make the app testable
        self.flask_app.config['TESTING'] = True
        self.app = self.flask_app.test_client()

    def test_double_instrumentation(self):
        # ensure Flask is never instrumented twice when `ddtrace-run`
        # and `TraceMiddleware` are used together. `traced_app` MUST
        # be assigned otherwise it's not possible to reproduce the
        # problem (the test scope must keep a strong reference)
        traced_app = TraceMiddleware(self.flask_app, self.tracer)  # noqa
        rv = self.app.get('/child')
        eq_(rv.status_code, 200)
        spans = self.tracer.writer.pop()
        eq_(len(spans), 2)

    def test_double_instrumentation_config(self):
        # ensure Flask uses the last set configuration to be sure
        # there are no breaking changes for who uses `ddtrace-run`
        # with the `TraceMiddleware`
        TraceMiddleware(
            self.flask_app,
            self.tracer,
            service='new-intake',
            distributed_tracing=False,
        )
        eq_(self.flask_app._service, 'new-intake')
        ok_(self.flask_app._use_distributed_tracing is False)
        rv = self.app.get('/child')
        eq_(rv.status_code, 200)
        spans = self.tracer.writer.pop()
        eq_(len(spans), 2)

    def test_child(self):
        start = time.time()
        rv = self.app.get('/child')
        end = time.time()
        # ensure request worked
        eq_(rv.status_code, 200)
        eq_(rv.data, b'child')
        # ensure trace worked
        spans = self.tracer.writer.pop()
        eq_(len(spans), 2)

        spans_by_name = {s.name:s for s in spans}

        s = spans_by_name['flask.request']
        assert s.span_id
        assert s.trace_id
        assert not s.parent_id
        eq_(s.service, 'test.flask.service')
        eq_(s.resource, "child")
        assert s.start >= start
        assert s.duration <= end - start
        eq_(s.error, 0)

        c = spans_by_name['child']
        assert c.span_id
        eq_(c.trace_id, s.trace_id)
        eq_(c.parent_id, s.span_id)
        eq_(c.service, 'test.flask.service')
        eq_(c.resource, 'child')
        assert c.start >= start
        assert c.duration <= end - start
        eq_(c.error, 0)

    def test_success(self):
        start = time.time()
        rv = self.app.get('/')
        end = time.time()

        # ensure request worked
        eq_(rv.status_code, 200)
        eq_(rv.data, b'hello')

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.service, 'test.flask.service')
        eq_(s.resource, "index")
        assert s.start >= start
        assert s.duration <= end - start
        eq_(s.error, 0)
        eq_(s.meta.get(http.STATUS_CODE), '200')
        eq_(s.meta.get(http.METHOD), 'GET')

        services = self.tracer.writer.pop_services()
        expected = {
            "test.flask.service": {"app":"flask", "app_type":"web"}
        }
        eq_(services, expected)

    def test_template(self):
        start = time.time()
        rv = self.app.get('/tmpl')
        end = time.time()

        # ensure request worked
        eq_(rv.status_code, 200)
        eq_(rv.data, b'hello earth')

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 2)
        by_name = {s.name:s for s in spans}
        s = by_name["flask.request"]
        eq_(s.service, "test.flask.service")
        eq_(s.resource, "tmpl")
        assert s.start >= start
        assert s.duration <= end - start
        eq_(s.error, 0)
        eq_(s.meta.get(http.STATUS_CODE), '200')
        eq_(s.meta.get(http.METHOD), 'GET')

        t = by_name["flask.template"]
        eq_(t.get_tag("flask.template"), "test.html")
        eq_(t.parent_id, s.span_id)
        eq_(t.trace_id, s.trace_id)
        assert s.start < t.start < t.start + t.duration < end

    def test_handleme(self):
        start = time.time()
        rv = self.app.get('/handleme')
        end = time.time()

        # ensure request worked
        eq_(rv.status_code, 202)
        eq_(rv.data, b'handled')

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.service, "test.flask.service")
        eq_(s.resource, "handle_me")
        assert s.start >= start
        assert s.duration <= end - start
        eq_(s.error, 0)
        eq_(s.meta.get(http.STATUS_CODE), '202')
        eq_(s.meta.get(http.METHOD), 'GET')

    def test_template_err(self):
        start = time.time()
        try:
            self.app.get('/tmpl/err')
        except Exception:
            pass
        else:
            assert 0
        end = time.time()

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        by_name = {s.name:s for s in spans}
        s = by_name["flask.request"]
        eq_(s.service, "test.flask.service")
        eq_(s.resource, "tmpl_err")
        assert s.start >= start
        assert s.duration <= end - start
        eq_(s.error, 1)
        eq_(s.meta.get(http.STATUS_CODE), '500')
        eq_(s.meta.get(http.METHOD), 'GET')

    def test_template_render_err(self):
        self.tracer.debug_logging = True
        start = time.time()
        try:
            self.app.get('/tmpl/render_err')
        except Exception:
            pass
        else:
            assert 0
        end = time.time()

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 2)
        by_name = {s.name:s for s in spans}
        s = by_name["flask.request"]
        eq_(s.service, "test.flask.service")
        eq_(s.resource, "tmpl_render_err")
        assert s.start >= start
        assert s.duration <= end - start
        eq_(s.error, 1)
        eq_(s.meta.get(http.STATUS_CODE), '500')
        eq_(s.meta.get(http.METHOD), 'GET')
        t = by_name["flask.template"]
        eq_(t.get_tag("flask.template"), "render_err.html")
        eq_(t.error, 1)
        eq_(t.parent_id, s.span_id)
        eq_(t.trace_id, s.trace_id)

    def test_error(self):
        start = time.time()
        rv = self.app.get('/error')
        end = time.time()

        # ensure the request itself worked
        eq_(rv.status_code, 500)
        eq_(rv.data, b'error')

        # ensure the request was traced.
        assert not self.tracer.current_span()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.service, "test.flask.service")
        eq_(s.resource, "error")
        assert s.start >= start
        assert s.duration <= end - start
        eq_(s.meta.get(http.STATUS_CODE), '500')
        eq_(s.meta.get(http.METHOD), 'GET')

    def test_fatal(self):
        if not self.traced_app.use_signals:
            return

        start = time.time()
        try:
            self.app.get('/fatal')
        except ZeroDivisionError:
            pass
        else:
            assert 0
        end = time.time()

        # ensure the request was traced.
        assert not self.tracer.current_span()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.service, "test.flask.service")
        eq_(s.resource, "fatal")
        assert s.start >= start
        assert s.duration <= end - start
        eq_(s.meta.get(http.STATUS_CODE), '500')
        eq_(s.meta.get(http.METHOD), 'GET')
        assert "ZeroDivisionError" in s.meta.get(errors.ERROR_TYPE), s.meta
        assert "by zero" in s.meta.get(errors.ERROR_MSG)
        assert re.search('File ".*/contrib/flask/web.py", line [0-9]+, in fatal', s.meta.get(errors.ERROR_STACK))

    def test_unicode(self):
        start = time.time()
        rv = self.app.get(u'/üŋïĉóđē')
        end = time.time()

        # ensure request worked
        eq_(rv.status_code, 200)
        eq_(rv.data, b'\xc3\xbc\xc5\x8b\xc3\xaf\xc4\x89\xc3\xb3\xc4\x91\xc4\x93')

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.service, "test.flask.service")
        eq_(s.resource, u'üŋïĉóđē')
        assert s.start >= start
        assert s.duration <= end - start
        eq_(s.error, 0)
        eq_(s.meta.get(http.STATUS_CODE), '200')
        eq_(s.meta.get(http.METHOD), 'GET')
        eq_(s.meta.get(http.URL), u'http://localhost/üŋïĉóđē')

    def test_404(self):
        start = time.time()
        rv = self.app.get(u'/404/üŋïĉóđē')
        end = time.time()

        # ensure that we hit a 404
        eq_(rv.status_code, 404)

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.service, "test.flask.service")
        eq_(s.resource, u'404')
        assert s.start >= start
        assert s.duration <= end - start
        eq_(s.error, 0)
        eq_(s.meta.get(http.STATUS_CODE), '404')
        eq_(s.meta.get(http.METHOD), 'GET')
        eq_(s.meta.get(http.URL), u'http://localhost/404/üŋïĉóđē')

    def test_propagation(self):
        rv = self.app.get('/', headers={
            'x-datadog-trace-id': '1234',
            'x-datadog-parent-id': '4567',
            'x-datadog-sampling-priority': '2'
        })

        # ensure request worked
        eq_(rv.status_code, 200)
        eq_(rv.data, b'hello')

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]

        # ensure the propagation worked well
        eq_(s.trace_id, 1234)
        eq_(s.parent_id, 4567)
        eq_(s.get_metric(SAMPLING_PRIORITY_KEY), 2)

    def test_custom_span(self):
        rv = self.app.get('/custom_span')
        eq_(rv.status_code, 200)
        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.service, "test.flask.service")
        eq_(s.resource, "overridden")
        eq_(s.error, 0)
        eq_(s.meta.get(http.STATUS_CODE), '200')
        eq_(s.meta.get(http.METHOD), 'GET')

    def test_success_200_ot(self):
        """OpenTracing version of test_success_200."""
        ot_tracer = init_tracer('my_svc', self.tracer)
        writer = self.tracer.writer

        with ot_tracer.start_active_span('ot_span'):
            start = time.time()
            rv = self.app.get('/')
            end = time.time()

        # ensure request worked
        eq_(rv.status_code, 200)
        eq_(rv.data, b'hello')

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = writer.pop()
        eq_(len(spans), 2)
        ot_span, dd_span = spans

        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(ot_span.resource, 'ot_span')
        eq_(ot_span.service, 'my_svc')

        eq_(dd_span.resource, "index")
        assert dd_span.start >= start
        assert dd_span.duration <= end - start
        eq_(dd_span.error, 0)
        eq_(dd_span.meta.get(http.STATUS_CODE), '200')
        eq_(dd_span.meta.get(http.METHOD), 'GET')
