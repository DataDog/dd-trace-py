import bottle
import webtest

import ddtrace
from ddtrace.internal import compat
from tests.utils import DummyTracer
from tests.utils import TracerTestCase
from tests.utils import assert_span_http_status_code


SERVICE = "bottle-app"


class TraceBottleTest(TracerTestCase):
    """
    Ensures that Bottle is properly traced.
    """

    def setUp(self):
        # provide a dummy tracer
        self.tracer = DummyTracer()
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
        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self._trace_app(self.tracer)

        # make a request
        resp = self.app.get("/hi/dougie")
        assert resp.status_int == 200
        assert compat.to_unicode(resp.body) == u"hi dougie"
        # validate it's traced
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == "bottle.request"
        assert s.service == "bottle-app"
        assert s.resource == "GET /hi/<name>"
        assert_span_http_status_code(s, 200)
        assert s.get_tag("http.method") == "GET"

    def test_500(self):
        @self.app.route("/hi")
        def hi():
            raise Exception("oh no")

        self._trace_app(self.tracer)

        # make a request
        try:
            resp = self.app.get("/hi")
            assert resp.status_int == 500
        except Exception:
            pass

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == "bottle.request"
        assert s.service == "bottle-app"
        assert s.resource == "GET /hi"
        assert_span_http_status_code(s, 500)
        assert s.error == 1
        assert s.get_tag("http.method") == "GET"

    def test_bottle_global_tracer(self):
        # without providing a Tracer instance, it should work
        @self.app.route("/home/")
        def home():
            return "Hello world"

        self._trace_app()

        # make a request
        resp = self.app.get("/home/")
        assert resp.status_int == 200
        # validate it's traced
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == "bottle.request"
        assert s.service == "bottle-app"
        assert s.resource == "GET /home/"
        assert_span_http_status_code(s, 200)
        assert s.get_tag("http.method") == "GET"
