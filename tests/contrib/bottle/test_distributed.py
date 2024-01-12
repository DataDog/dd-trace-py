import bottle
import webtest

import ddtrace
from ddtrace.contrib.bottle import TracePlugin
from ddtrace.internal import compat
from tests.utils import TracerTestCase
from tests.utils import assert_span_http_status_code


SERVICE = "bottle-app"


class TraceBottleDistributedTest(TracerTestCase):
    """
    Ensures that Bottle is properly traced.
    """

    def setUp(self):
        super(TraceBottleDistributedTest, self).setUp()

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

    def _trace_app_distributed(self, tracer=None):
        ddtrace.config.bottle["distributed_tracing"] = True
        self._trace_app(tracer=tracer)

    def _trace_app_not_distributed(self, tracer=None):
        ddtrace.config.bottle["distributed_tracing"] = False
        self._trace_app(tracer=tracer)

    def test_distributed(self):
        # setup our test app
        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self._trace_app_distributed(self.tracer)

        # make a request
        headers = {"x-datadog-trace-id": "123", "x-datadog-parent-id": "456"}
        resp = self.app.get("/hi/dougie", headers=headers)
        assert resp.status_int == 200
        assert compat.to_unicode(resp.body) == "hi dougie"

        # validate it's traced
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == "bottle.request"
        assert s.service == "bottle-app"
        assert s.resource == "GET /hi/<name>"
        assert_span_http_status_code(s, 200)
        assert s.get_tag("http.method") == "GET"
        assert s.get_tag("component") == "bottle"
        assert s.get_tag("span.kind") == "server"
        # check distributed headers
        assert 123 == s.trace_id
        assert 456 == s.parent_id

    def test_not_distributed(self):
        # setup our test app
        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self._trace_app_not_distributed(self.tracer)

        # make a request
        headers = {"x-datadog-trace-id": "123", "x-datadog-parent-id": "456"}
        resp = self.app.get("/hi/dougie", headers=headers)
        assert resp.status_int == 200
        assert compat.to_unicode(resp.body) == "hi dougie"

        # validate it's traced
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == "bottle.request"
        assert s.service == "bottle-app"
        assert s.resource == "GET /hi/<name>"
        assert_span_http_status_code(s, 200)
        assert s.get_tag("http.method") == "GET"
        assert s.get_tag("component") == "bottle"
        assert s.get_tag("span.kind"), "server"
        # check distributed headers
        assert 123 != s.trace_id
        assert 456 != s.parent_id

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_BOTTLE_DISTRIBUTED_TRACING="False"))
    def test_distributed_tracing_disabled_via_env_var(self):
        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self._trace_app(self.tracer)

        # make a request
        headers = {"x-datadog-trace-id": "123", "x-datadog-parent-id": "456"}
        resp = self.app.get("/hi/dougie", headers=headers)
        assert resp.status_int == 200
        assert compat.to_unicode(resp.body) == "hi dougie"

        # validate it's traced
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == "bottle.request"
        assert s.service == "bottle-app"
        assert s.resource == "GET /hi/<name>"
        assert_span_http_status_code(s, 200)
        assert s.get_tag("http.method") == "GET"
        assert s.get_tag("component") == "bottle"
        assert s.get_tag("span.kind"), "server"
        # check distributed headers
        assert 123 != s.trace_id
        assert 456 != s.parent_id
