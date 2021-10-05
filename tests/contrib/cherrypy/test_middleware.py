# -*- coding: utf-8 -*-
import logging
import re
import time

import cherrypy
from cherrypy.test import helper
from six.moves.urllib.parse import quote as url_quote

import ddtrace
from ddtrace import config
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.contrib.cherrypy import TraceMiddleware
from ddtrace.ext import http
from tests.utils import TracerTestCase
from tests.utils import assert_span_http_status_code
from tests.utils import snapshot

from .web import TestApp


logger = logging.getLogger()
logger.level = logging.DEBUG


class TestCherrypy(TracerTestCase, helper.CPWebCase):
    """
    FIXME: the tests using getPage() are not synchronous and so require a
           delay afterwards.
    """

    @staticmethod
    def setup_server():
        cherrypy.tree.mount(
            TestApp(),
            "/",
            {
                "/": {"tools.tracer.on": True},
            },
        )

    def setUp(self):
        super(TestCherrypy, self).setUp()
        self.traced_app = TraceMiddleware(
            cherrypy,
            self.tracer,
            service="test.cherrypy.service",
            distributed_tracing=True,
        )

    def test_double_instrumentation(self):
        # ensure CherryPy is never instrumented twice when `ddtrace-run`
        # and `TraceMiddleware` are used together. `traced_app` MUST
        # be assigned otherwise it's not possible to reproduce the
        # problem (the test scope must keep a strong reference)
        traced_app = TraceMiddleware(cherrypy, self.tracer)  # noqa: F841
        self.getPage("/")
        time.sleep(0.1)
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertStatus("200 OK")

        spans = self.pop_spans()
        assert len(spans) == 1

    def test_double_instrumentation_config(self):
        # ensure CherryPy uses the last set configuration to be sure
        # there are no breaking changes for who uses `ddtrace-run`
        # with the `TraceMiddleware`
        assert cherrypy.tools.tracer.service == "test.cherrypy.service"
        TraceMiddleware(
            cherrypy,
            self.tracer,
            service="new-intake",
        )
        self.getPage("/")
        time.sleep(0.1)
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertStatus("200 OK")

        spans = self.pop_spans()
        assert len(spans) == 1

        assert cherrypy.tools.tracer.service == "new-intake"

    def test_child(self):
        self.getPage("/child")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("child")

        # ensure trace worked
        spans = self.pop_spans()
        assert len(spans) == 2

        spans_by_name = {s.name: s for s in spans}

        s = spans_by_name["cherrypy.request"]
        assert s.span_id
        assert s.trace_id
        assert not s.parent_id
        assert s.service == "test.cherrypy.service"
        assert s.resource == "GET /child"
        assert s.error == 0

        c = spans_by_name["child"]
        assert c.span_id
        assert c.trace_id == s.trace_id
        assert c.parent_id == s.span_id
        assert c.service == "test.cherrypy.service"
        assert c.resource == "child"
        assert c.error == 0

    def test_success(self):
        self.getPage("/")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "GET /"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.meta.get(http.METHOD) == "GET"

    def test_alias(self):
        self.getPage("/aliases")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("alias")

        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "GET /aliases"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.meta.get(http.METHOD) == "GET"

    def test_handleme(self):
        self.getPage("/handleme")
        time.sleep(0.1)
        self.assertErrorPage(418, message="handled")

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "GET /handleme"
        assert s.error == 0
        assert_span_http_status_code(s, 418)
        assert s.meta.get(http.METHOD) == "GET"

    def test_error(self):
        self.getPage("/error")
        time.sleep(0.1)
        self.assertErrorPage(500)

        # ensure the request was traced.
        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "GET /error"
        assert_span_http_status_code(s, 500)
        assert s.error == 1
        assert s.meta.get(http.METHOD) == "GET"

    def test_fatal(self):
        self.getPage("/fatal")
        time.sleep(0.1)

        self.assertErrorPage(500)

        assert not self.tracer.current_span()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "GET /fatal"
        assert_span_http_status_code(s, 500)
        assert s.error == 1
        assert s.meta.get(http.METHOD) == "GET"
        assert "ZeroDivisionError" in s.meta.get(ERROR_TYPE), s.meta
        assert "by zero" in s.meta.get(ERROR_MSG)
        assert re.search('File ".*/contrib/cherrypy/web.py", line [0-9]+, in fatal', s.meta.get(ERROR_STACK))

    def test_unicode(self):
        # Encoded utf8 query strings MUST be parsed correctly.
        # Here, the URL is encoded in utf8 and then %HEX
        # See https://docs.cherrypy.org/en/latest/_modules/cherrypy/test/test_encoding.html for more
        self.getPage(url_quote(u"/üŋïĉóđē".encode("utf-8")))
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody(b"\xc3\xbc\xc5\x8b\xc3\xaf\xc4\x89\xc3\xb3\xc4\x91\xc4\x93")

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == u"GET /üŋïĉóđē"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.meta.get(http.METHOD) == "GET"
        assert s.meta.get(http.URL) == u"http://127.0.0.1:54583/üŋïĉóđē"

    def test_404(self):
        self.getPage(u"/404/test")
        time.sleep(0.1)
        self.assertStatus("404 Not Found")

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == u"GET /404/test"
        assert s.error == 0
        assert_span_http_status_code(s, 404)
        assert s.meta.get(http.METHOD) == "GET"
        assert s.meta.get(http.URL) == u"http://127.0.0.1:54583/404/test"

    def test_propagation(self):
        self.getPage(
            "/",
            headers=[
                ("x-datadog-trace-id", "1234"),
                ("x-datadog-parent-id", "4567"),
                ("x-datadog-sampling-priority", "2"),
            ],
        )
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        # ensure the propagation worked well
        assert s.trace_id == 1234
        assert s.parent_id == 4567
        assert s.get_metric(SAMPLING_PRIORITY_KEY) == 2

    def test_disabled_distributed_tracing_config(self):
        previous_distributed_tracing = config.cherrypy["distributed_tracing"]
        config.cherrypy["distributed_tracing"] = False
        self.getPage(
            "/",
            headers=[
                ("x-datadog-trace-id", "1234"),
                ("x-datadog-parent-id", "4567"),
                ("x-datadog-sampling-priority", "2"),
            ],
        )
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        # ensure the propagation worked well
        assert s.trace_id != 1234
        assert s.parent_id != 4567
        assert s.get_metric(SAMPLING_PRIORITY_KEY) != 2

        config.cherrypy["distributed_tracing"] = previous_distributed_tracing

    def test_disabled_distributed_tracing_middleware(self):
        previous_distributed_tracing = cherrypy.tools.tracer.use_distributed_tracing
        cherrypy.tools.tracer.use_distributed_tracing = False
        self.getPage(
            "/",
            headers=[
                ("x-datadog-trace-id", "1234"),
                ("x-datadog-parent-id", "4567"),
                ("x-datadog-sampling-priority", "2"),
            ],
        )
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        # ensure the propagation worked well
        assert s.trace_id != 1234
        assert s.parent_id != 4567
        assert s.get_metric(SAMPLING_PRIORITY_KEY) != 2

        cherrypy.tools.tracer.use_distributed_tracing = previous_distributed_tracing

    def test_custom_span(self):
        self.getPage(u"/custom_span")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertBody("hiya")

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "overridden"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.meta.get(http.METHOD) == "GET"

    def test_http_request_header_tracing(self):
        config.cherrypy.http.trace_headers(["Host", "my-header"])
        self.getPage(
            "/",
            headers=[
                ("my-header", "my_value"),
                ("x-datadog-parent-id", "4567"),
                ("x-datadog-sampling-priority", "2"),
            ],
        )
        time.sleep(0.1)

        traces = self.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]
        assert span.get_tag("http.request.headers.my-header") == "my_value"
        assert span.get_tag("http.request.headers.host") == "127.0.0.1:54583"

    def test_http_response_header_tracing(self):
        config.cherrypy.http.trace_headers(["my-response-header"])
        self.getPage("/response_headers")
        time.sleep(0.1)

        traces = self.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]

        assert span.get_tag("http.response.headers.my-response-header") == "my_response_value"

    def test_variable_resource(self):
        self.getPage("/dispatch/abc123/")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("dispatch with abc123")

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"

        # Once CherryPy returns sensible results for virtual path components, this
        # can be: "GET /dispatch/{{test_value}}/"
        assert s.resource == "GET /dispatch/abc123/"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.meta.get(http.METHOD) == "GET"

    def test_post(self):
        self.getPage("/", method="POST")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "test.cherrypy.service"
        assert s.resource == "POST /"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.meta.get(http.METHOD) == "POST"

    def test_service_configuration_config(self):
        previous_service = config.cherrypy.get("service", "test.cherrypy.service")
        config.cherrypy["service"] = "my_cherrypy_service"
        self.getPage("/")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "my_cherrypy_service"
        assert s.resource == "GET /"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.meta.get(http.METHOD) == "GET"

        config.cherrypy["service"] = previous_service

    def test_service_configuration_middleware(self):
        previous_service = cherrypy.tools.tracer.service
        cherrypy.tools.tracer.service = "my_cherrypy_service2"
        self.getPage("/")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

        # ensure trace worked
        assert not self.tracer.current_span(), self.tracer.current_span().pprint()
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "my_cherrypy_service2"
        assert s.resource == "GET /"
        assert s.error == 0
        assert_span_http_status_code(s, 200)
        assert s.meta.get(http.METHOD) == "GET"

        cherrypy.tools.tracer.service = previous_service


class TestCherrypySnapshot(helper.CPWebCase):
    @staticmethod
    def setup_server():
        cherrypy.tree.mount(
            TestApp(),
            "/",
            {
                "/": {"tools.tracer.on": True},
            },
        )

    def setUp(self):
        self.traced_app = TraceMiddleware(
            cherrypy,
            tracer=ddtrace.tracer,
            service="test.cherrypy.service",
            distributed_tracing=True,
        )

    @snapshot()
    def test_child(self):
        self.getPage("/child")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("child")

    @snapshot(ignores=["meta.error.stack", "meta.error.type", "meta.error.msg"])
    def test_success(self):
        self.getPage("/")
        time.sleep(0.1)
        self.assertStatus("200 OK")
        self.assertHeader("Content-Type", "text/html;charset=utf-8")
        self.assertBody("Hello world!")

    @snapshot(ignores=["meta.error.stack", "meta.error.type", "meta.error.msg"])
    def test_error(self):
        self.getPage("/error")
        time.sleep(0.1)
        self.assertErrorPage(500)

    @snapshot(ignores=["meta.error.stack", "meta.error.type", "meta.error.msg"])
    def test_fatal(self):
        self.getPage("/fatal")
        time.sleep(0.1)
        self.assertErrorPage(500)
