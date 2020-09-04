"""
Cyclone provides unittest style helpers so stick to a unittest style suite.
"""

import cyclone
import cyclone.web
from cyclone.testing import CycloneTestCase
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks

from ddtrace import config, Pin
from ddtrace.contrib.cyclone import patch, unpatch

from tests import TracerTestCase, TracerSpanContainer


def mk_app():
    class RootHandler(cyclone.web.RequestHandler):
        def get(self):
            self.set_header("resp-header", "test-value")
            self.write("Hello, world")

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

    app = cyclone.web.Application([(r"/", RootHandler), (r"/error", ErrHandler), (r"/async-sleep", AsyncHandler)])
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

        assert len(self.test_spans.get_spans()) == 1
        span = self.test_spans.get_root_span()
        assert span.name == "cyclone.request"
        assert span.resource == "tests.contrib.cyclone.test_cyclone.RootHandler"
        assert span.service == "cyclone"
        assert span.error == 0
        assert span.span_type == "web"
        assert span.get_tag("http.method") == "GET"
        assert span.get_tag("http.status_code") == "200"
        assert span.get_tag("http.url") == "http://127.0.0.1/"
        self.assert_is_measured(span)

    @inlineCallbacks
    def test_request_error(self):
        resp = yield self.client.get("/error")
        assert (
            resp.content
            == b"<html><title>500: Internal Server Error</title><body>500: Internal Server Error</body></html>"
        )
        assert resp.get_status() == 500
        assert len(self.test_spans.get_spans()) == 1
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

    def test_concurrent_requests(self):
        self.client.get("/async-sleep?k=1")
        self.client.get("/async-sleep?k=2")
        reactor.callLater(0.5, reactor.stop)
        reactor.run()

        spans = self.test_spans.get_spans()
        assert len(spans) == 2

        s1, s2 = spans
        # This will fail until twisted context propagation is supported.
        assert s1.trace_id != s2.trace_id
