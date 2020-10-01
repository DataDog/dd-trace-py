"""
Cyclone provides unittest style helpers so stick to a unittest style suite.
"""
import os

import cyclone
import cyclone.web
from cyclone import template
from .testcase import CycloneTestCase
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks

from ddtrace import config, Pin
from ddtrace.contrib.cyclone import patch, unpatch

from tests import TracerTestCase, TracerSpanContainer


def mk_app():
    class PostModule(cyclone.web.UIModule):
        def render(self, title, body):
            return self.render_string("post.html", title=title, body=body)

    class RootHandler(cyclone.web.RequestHandler):
        def get(self):
            self.set_header("resp-header", "test-value")
            self.write("Hello, world")

    class TraceHandler(cyclone.web.RequestHandler):
        def get(self):
            from ddtrace import tracer

            with tracer.trace("child"):
                pass

    class AsyncTraceHandler(cyclone.web.RequestHandler):
        @cyclone.web.asynchronous
        def get(self):
            from ddtrace import tracer

            with tracer.trace("child"):
                pass

            reactor.callLater(0.1, self.done)

        def done(self):
            self.finish("done")

    class ErrHandler(cyclone.web.RequestHandler):
        def get(self):
            raise Exception("uh oh!")

    class AsyncHandler(cyclone.web.RequestHandler):
        @cyclone.web.asynchronous
        def get(self):
            self.write("Processing...")
            reactor.callLater(0.1, self.done)

        def done(self):
            self.finish("done")

    class TemplateHandler(cyclone.web.RequestHandler):
        def get_template_path(self):
            return os.path.dirname(os.path.realpath(__file__))

        @cyclone.web.asynchronous
        def get(self):
            items = ["Item 1", "Item 2", "Item 3"]
            self.render("template.html", title="My title", items=items)

    class UIModuleHandler(cyclone.web.RequestHandler):
        @cyclone.web.asynchronous
        def get(self):
            posts = [
                ("Michael Scott", "My mind is going a mile an hour"),
                ("Kevin Malone", "Why waste time say lot word when few word do trick"),
                (
                    "Darryl Philbin",
                    (
                        "I decided to stay home, eat a bunch of tacos in my basement. "
                        "Now my basement smells like tacos. You can't air out a basement. "
                        "And taco air is heavy. It settles at the lowest point"
                    ),
                ),
            ]
            self.render("uimodules.html", posts=posts)

    app = cyclone.web.Application(
        handlers=[
            (r"/", RootHandler),
            (r"/error", ErrHandler),
            (r"/async-sleep", AsyncHandler),
            (r"/trace", TraceHandler),
            (r"/async-trace", AsyncTraceHandler),
            (r"/template", TemplateHandler),
            (r"/uimodules", UIModuleHandler),
        ],
        template_path=os.path.dirname(__file__),
        ui_modules={"Post": PostModule},
    )
    return app


class TestCylcone(CycloneTestCase, TracerTestCase):
    app_builder = staticmethod(mk_app)

    def setUp(self):
        super(TestCylcone, self).setUp()

        patch()
        pin = Pin.get_from(cyclone)
        self.original_tracer = pin.tracer
        Pin.override(cyclone, tracer=self.tracer)

    def tearDown(self):
        unpatch()
        Pin.override(cyclone, tracer=self.original_tracer)
        self.reset()

    @property
    def test_spans(self):
        return TracerSpanContainer(self.tracer)

    @inlineCallbacks
    def test_request(self):
        resp = yield self.client.get("/")
        assert resp.content == b"Hello, world"
        assert resp.get_status() == 200

        assert len(self.test_spans.get_spans()) == 2
        span = self.test_spans.get_root_span()
        assert span.name == "cyclone.request"
        assert span.resource == "tests.contrib.cyclone.test_cyclone.RootHandler"
        assert span.get_tag("cyclone.handler") == "tests.contrib.cyclone.test_cyclone.RootHandler"
        assert span.service == "cyclone"
        assert span.error == 0
        assert span.span_type == "web"
        assert span.get_tag("http.method") == "GET"
        assert span.get_tag("http.status_code") == "200"
        assert span.get_tag("http.url") == "http://127.0.0.1/"
        self.assert_is_measured(span)

        _, span2 = self.test_spans.get_spans()
        assert span2.name == "cyclone.request.get"
        assert span2.resource == "cyclone.request.get"
        assert span2.service == "cyclone"
        assert span2.error == 0

    @inlineCallbacks
    def test_request_error(self):
        resp = yield self.client.get("/error")
        assert (
            resp.content
            == b"<html><title>500: Internal Server Error</title><body>500: Internal Server Error</body></html>"
        )
        assert resp.get_status() == 500
        assert len(self.test_spans.get_spans()) == 2
        span = self.test_spans.get_root_span()
        assert span.name == "cyclone.request"
        assert span.resource == "tests.contrib.cyclone.test_cyclone.ErrHandler"
        assert span.service == "cyclone"
        assert span.error == 1
        assert span.span_type == "web"
        assert span.get_tag("http.method") == "GET"
        assert span.get_tag("http.status_code") == "500"
        assert span.get_tag("http.url") == "http://127.0.0.1/error"
        assert span.get_tag("error.msg") == "uh oh!"
        assert "Exception: uh oh!" in span.get_tag("error.stack")

    @inlineCallbacks
    def test_service_name_app_default(self):
        with self.override_global_config(dict(service="app-svc")):
            resp = yield self.client.get("/")
            assert resp.get_status() == 200

        span = self.test_spans.get_root_span()
        assert span.service == "app-svc"

    @inlineCallbacks
    def test_analytics(self):
        with self.override_global_config(dict(analytics_enabled=False)):
            resp = yield self.client.get("/")
            assert resp.get_status() == 200

        span = self.test_spans.get_root_span()
        assert span.get_metric("_dd1.sr.eausr") is None

        self.reset()

        with self.override_global_config(dict(analytics_enabled=False)):
            with self.override_config("cyclone", dict(analytics_enabled=False, analytics_sample_rate=1.0)):
                resp = yield self.client.get("/")
                assert resp.get_status() == 200

        span = self.test_spans.get_root_span()
        assert span.get_metric("_dd1.sr.eausr") is None

    @inlineCallbacks
    def test_query_string_disabled(self):
        resp = yield self.client.get("/?key1=val1&key2=val2")
        assert resp.get_status() == 200
        span = self.test_spans.get_root_span()
        assert span.get_tag("http.query.string") is None

    @inlineCallbacks
    def test_query_string_enabled(self):
        with self.override_http_config("cyclone", dict(trace_query_string=True)):
            resp = yield self.client.get("/?key1=val1&key2=val2")
            assert resp.get_status() == 200

        span = self.test_spans.get_root_span()
        assert span.get_tag("http.query.string") == "key1=val1&key2=val2"

    @inlineCallbacks
    def test_headers_disabled(self):
        resp = yield self.client.get("/", headers={"my-header": "test-value"})
        assert resp.get_status() == 200

        span = self.test_spans.get_root_span()
        assert span.get_tag("http.request.headers.header") is None
        assert span.get_tag("http.response.headers.resp-header") is None

        self.reset()

        with self.override_http_config("cyclone", dict()):
            config.cyclone.http.trace_headers(["my-header", "resp-header"])
            resp = yield self.client.get("/", headers={"my-header": "test-value"})
            assert resp.get_status() == 200
            span = self.test_spans.get_root_span()
            assert span.get_tag("http.request.headers.my-header") == "test-value"
            assert span.get_tag("http.response.headers.resp-header") == "test-value"

    @inlineCallbacks
    def test_distributed_tracing_receive(self):
        resp = yield self.client.get(
            "/",
            headers={"x-datadog-trace-id": "1234321", "x-datadog-parent-id": "12", "x-datadog-sampling-priority": 2,},
        )
        assert resp.get_status() == 200

        span = self.test_spans.find_span(name="cyclone.request")
        span.assert_matches(trace_id=1234321, parent_id=12, metrics={"_sampling_priority_v1": 2})

    # def test_async_trace(self):
    #     self.client.get("/async-trace")
    #     reactor.callLater(0.5, reactor.stop)
    #     reactor.run()

    #     spans = self.test_spans.get_spans()
    #     assert len(spans) == 3

    #     s1, s2, s3 = spans
    #     assert s1.trace_id == s2.trace_id == s3.trace_id

    def test_concurrent_requests(self):
        self.client.get("/async-sleep?k=1")
        self.client.get("/async-sleep?k=2")
        self.client.get("/async-sleep?k=3")

        reactor.callLater(0.5, reactor.stop)
        reactor.run()

        spans = self.test_spans.get_spans()
        assert len(spans) == 6

        roots = self.test_spans.get_root_spans()
        assert len(roots) == 3

    @inlineCallbacks
    def test_handler_trace(self):
        with self.override_global_tracer(tracer=self.tracer):
            resp = yield self.client.get("/trace")
            assert resp.get_status() == 200

        spans = self.test_spans.get_spans()
        assert len(spans) == 3

        s1, s2, s3 = spans
        assert s1.trace_id == s2.trace_id == s3.trace_id
        assert s2.parent_id == s1.span_id
        assert s3.parent_id == s2.span_id

    @inlineCallbacks
    def test_handler_multi(self):
        resp = yield self.client.get("/")
        assert resp.get_status() == 200
        resp = yield self.client.get("/")
        assert resp.get_status() == 200

        spans = self.test_spans.get_spans()
        assert len(spans) == 4

        s1, s2 = self.test_spans.get_root_spans()
        assert s1.trace_id != s2.trace_id
        assert s1.parent_id is None
        assert s2.parent_id is None

    def test_template_manual(self):
        t = template.Template("<html>{{ myvalue }}</html>")
        assert t.generate(myvalue="34") == b"<html>34</html>"

    @inlineCallbacks
    def test_template(self):
        resp = yield self.client.get("/template")
        assert resp.get_status() == 200

        spans = self.test_spans.get_spans()
        assert len(spans) == 4

        s1, s2, s3, s4 = spans
        assert s1.error == s2.error == s3.error == s4.error == 0
        assert s1.trace_id == s2.trace_id
        assert s2.parent_id == s1.span_id

        assert s3.name == "cyclone.request.render_string"
        assert s3.span_type == "template"
        assert s3.get_tag("template_name") == "template.html"

        assert s4.parent_id == s3.span_id
        assert s4.name == "cyclone.template.generate"
        assert s4.span_type == "template"

    @inlineCallbacks
    def test_template_error(self):
        resp = yield self.client.get("/template")
        assert resp.get_status() == 200

        spans = self.test_spans.get_spans()
        assert len(spans) == 4

    def test_template_concurrent(self):
        self.client.get("/template")
        self.client.get("/template")
        self.client.get("/template")

        self.assert_trace_count(3)

        spans = self.test_spans.get_spans()
        assert len(spans) == 12

        assert len(self.get_root_spans()) == 3
        r1, r2, r3 = self.get_root_spans()

        spans = list(self.filter_spans(trace_id=r1.trace_id, parent_id=None))
        assert len(spans) == 1

    @inlineCallbacks
    def test_uimodules(self):
        yield self.client.get("/uimodules")

        self.assert_trace_count(1)
        spans = self.test_spans.get_spans()
        assert len(spans) == 10
