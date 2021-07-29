# -*- coding: utf-8 -*-
import re
import time

from flask import make_response

from ddtrace import config
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.contrib.flask import TraceMiddleware
from ddtrace.ext import errors
from ddtrace.ext import http
from tests.opentracer.utils import init_tracer
from tests.utils import DummyTracer
from tests.utils import TracerTestCase
from tests.utils import assert_span_http_status_code

from .web import create_app


class TestFlask(TracerTestCase):
    """Ensures Flask is properly instrumented."""

    def setUp(self):
        self.tracer = DummyTracer()
        self.app = create_app()
        self.traced_app = TraceMiddleware(
            self.app,
            self.tracer,
            service="test.flask.service",
            distributed_tracing=True,
        )

        # make the app testable
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_double_instrumentation(self):
        # ensure Flask is never instrumented twice when `ddtrace-run`
        # and `TraceMiddleware` are used together. `traced_app` MUST
        # be assigned otherwise it's not possible to reproduce the
        # problem (the test scope must keep a strong reference)
        traced_app = TraceMiddleware(self.app, self.tracer)  # noqa: F841
        rv = self.client.get("/child")
        assert rv.status_code == 200
        spans = self.pop_spans()
        assert len(spans) == 2

    def test_double_instrumentation_config(self):
        # ensure Flask uses the last set configuration to be sure
        # there are no breaking changes for who uses `ddtrace-run`
        # with the `TraceMiddleware`
        TraceMiddleware(
            self.app,
            self.tracer,
            service="new-intake",
            distributed_tracing=False,
        )
        assert self.app._service == "new-intake"
        assert self.app._use_distributed_tracing is False
        rv = self.client.get("/child")
        assert rv.status_code == 200
        spans = self.pop_spans()
        assert len(spans) == 2

    def test_child(self):
        start = time.time()
        rv = self.client.get("/child")
        end = time.time()
        # ensure request worked
        assert rv.status_code == 200
        assert rv.data == b"child"
        # ensure trace worked
        spans = self.pop_spans()
        assert len(spans) == 2

        spans_by_name = {s.name: s for s in spans}

        s = spans_by_name["flask.request"]
        assert s.span_id
        assert s.trace_id
        assert not s.parent_id
        assert s.service == "test.flask.service"
        assert s.resource == "child"
        assert s.start >= start
        assert s.duration <= end - start
        assert s.error == 0

        c = spans_by_name["child"]
        assert c.span_id
        assert c.trace_id == s.trace_id
        assert c.parent_id == s.span_id
        assert c.service == "test.flask.service"
        assert c.resource == "child"
        assert c.start >= start
        assert c.duration <= end - start
        assert c.error == 0

    def test_success(self):
        start = time.time()
        rv = self.client.get("/")
        end = time.time()

        # ensure request worked
        assert rv.status_code == 200
        assert rv.data == b"hello"

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.flask.service"
        assert s.resource == "index"
        assert s.start >= start
        assert s.duration <= end - start
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.meta.get(http.METHOD) == "GET"

    def test_template(self):
        start = time.time()
        rv = self.client.get("/tmpl")
        end = time.time()

        # ensure request worked
        assert rv.status_code == 200
        assert rv.data == b"hello earth"

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 2
        by_name = {s.name: s for s in spans}
        s = by_name["flask.request"]
        assert s.service == "test.flask.service"
        assert s.resource == "tmpl"
        assert s.start >= start
        assert s.duration <= end - start
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.meta.get(http.METHOD) == "GET"

        t = by_name["flask.template"]
        assert t.get_tag("flask.template") == "test.html"
        assert t.parent_id == s.span_id
        assert t.trace_id == s.trace_id
        assert s.start < t.start < t.start + t.duration < end

    def test_handleme(self):
        start = time.time()
        rv = self.client.get("/handleme")
        end = time.time()

        # ensure request worked
        assert rv.status_code == 202
        assert rv.data == b"handled"

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.flask.service"
        assert s.resource == "handle_me"
        assert s.start >= start
        assert s.duration <= end - start
        assert s.error == 0
        assert_span_http_status_code(s, 202)
        assert s.meta.get(http.METHOD) == "GET"

    def test_template_err(self):
        start = time.time()
        try:
            self.client.get("/tmpl/err")
        except Exception:
            pass
        else:
            assert 0
        end = time.time()

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        by_name = {s.name: s for s in spans}
        s = by_name["flask.request"]
        assert s.service == "test.flask.service"
        assert s.resource == "tmpl_err"
        assert s.start >= start
        assert s.duration <= end - start
        assert s.error == 1
        assert_span_http_status_code(s, 500)
        assert s.meta.get(http.METHOD) == "GET"

    def test_template_render_err(self):
        start = time.time()
        try:
            self.client.get("/tmpl/render_err")
        except Exception:
            pass
        else:
            assert 0
        end = time.time()

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 2
        by_name = {s.name: s for s in spans}
        s = by_name["flask.request"]
        assert s.service == "test.flask.service"
        assert s.resource == "tmpl_render_err"
        assert s.start >= start
        assert s.duration <= end - start
        assert s.error == 1
        assert_span_http_status_code(s, 500)
        assert s.meta.get(http.METHOD) == "GET"
        t = by_name["flask.template"]
        assert t.get_tag("flask.template") == "render_err.html"
        assert t.error == 1
        assert t.parent_id == s.span_id
        assert t.trace_id == s.trace_id

    def test_error(self):
        start = time.time()
        rv = self.client.get("/error")
        end = time.time()

        # ensure the request itself worked
        assert rv.status_code == 500
        assert rv.data == b"error"

        # ensure the request was traced.
        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.flask.service"
        assert s.resource == "error"
        assert s.start >= start
        assert s.duration <= end - start
        assert_span_http_status_code(s, 500)
        assert s.error == 1
        assert s.meta.get(http.METHOD) == "GET"

    def test_fatal(self):
        if not self.traced_app.use_signals:
            return

        start = time.time()
        try:
            self.client.get("/fatal")
        except ZeroDivisionError:
            pass
        else:
            assert 0
        end = time.time()

        # ensure the request was traced.
        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.flask.service"
        assert s.resource == "fatal"
        assert s.start >= start
        assert s.duration <= end - start
        assert_span_http_status_code(s, 500)
        assert s.error == 1
        assert s.meta.get(http.METHOD) == "GET"
        assert "ZeroDivisionError" in s.meta.get(errors.ERROR_TYPE), s.meta
        assert "by zero" in s.meta.get(errors.ERROR_MSG)
        assert re.search('File ".*/contrib/flask/web.py", line [0-9]+, in fatal', s.meta.get(errors.ERROR_STACK))

    def test_unicode(self):
        start = time.time()
        rv = self.client.get(u"/üŋïĉóđē")
        end = time.time()

        # ensure request worked
        assert rv.status_code == 200
        assert rv.data == b"\xc3\xbc\xc5\x8b\xc3\xaf\xc4\x89\xc3\xb3\xc4\x91\xc4\x93"

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.flask.service"
        assert s.resource == u"üŋïĉóđē"
        assert s.start >= start
        assert s.duration <= end - start
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.meta.get(http.METHOD) == "GET"
        assert s.meta.get(http.URL) == u"http://localhost/üŋïĉóđē"

    def test_404(self):
        start = time.time()
        rv = self.client.get(u"/404/üŋïĉóđē")
        end = time.time()

        # ensure that we hit a 404
        assert rv.status_code == 404

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.flask.service"
        assert s.resource == u"404"
        assert s.start >= start
        assert s.duration <= end - start
        assert s.error == 0
        assert_span_http_status_code(s, 404)
        assert s.meta.get(http.METHOD) == "GET"
        assert s.meta.get(http.URL) == u"http://localhost/404/üŋïĉóđē"

    def test_propagation(self):
        rv = self.client.get(
            "/",
            headers={"x-datadog-trace-id": "1234", "x-datadog-parent-id": "4567", "x-datadog-sampling-priority": "2"},
        )

        # ensure request worked
        assert rv.status_code == 200
        assert rv.data == b"hello"

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        # ensure the propagation worked well
        assert s.trace_id == 1234
        assert s.parent_id == 4567
        assert s.get_metric(SAMPLING_PRIORITY_KEY) == 2

    def test_custom_span(self):
        rv = self.client.get("/custom_span")
        assert rv.status_code == 200
        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.flask.service"
        assert s.resource == "overridden"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.meta.get(http.METHOD) == "GET"

    def test_success_200_ot(self):
        """OpenTracing version of test_success_200."""
        ot_tracer = init_tracer("my_svc", self.tracer)

        with ot_tracer.start_active_span("ot_span"):
            start = time.time()
            rv = self.client.get("/")
            end = time.time()

        # ensure request worked
        assert rv.status_code == 200
        assert rv.data == b"hello"

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 2
        ot_span, dd_span = spans

        # confirm the parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id

        assert ot_span.resource == "ot_span"
        assert ot_span.service == "my_svc"

        assert dd_span.resource == "index"
        assert dd_span.start >= start
        assert dd_span.duration <= end - start
        assert dd_span.error == 0
        assert_span_http_status_code(dd_span, 200)
        assert dd_span.meta.get(http.METHOD) == "GET"

    def test_http_request_header_tracing(self):
        config.flask.http.trace_headers(["Host", "my-header"])
        self.client.get(
            "/",
            headers={
                "my-header": "my_value",
            },
        )
        traces = self.pop_traces()

        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]

        assert span.get_tag("http.request.headers.my-header") == "my_value"
        assert span.get_tag("http.request.headers.host") == "localhost"

    def test_http_response_header_tracing(self):
        config.flask.http.trace_headers(["my-response-header"])

        @self.app.route("/response_headers")
        def response_headers():
            resp = make_response("Hello Flask")
            resp.headers["my-response-header"] = "my_response_value"
            return resp

        self.client.get("/response_headers")

        traces = self.pop_traces()

        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]

        assert span.get_tag("http.response.headers.my-response-header") == "my_response_value"
