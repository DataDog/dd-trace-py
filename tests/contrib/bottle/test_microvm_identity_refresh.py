import bottle
import mock
import webtest

from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.contrib.internal.bottle.patch import TracePlugin
from ddtrace.contrib.internal.bottle.patch import patch
from ddtrace.internal import core
from tests.utils import TracerTestCase


REQUEST_STARTING_PATH = "/web-request-starting"


class BottleMicrovmIdentityRefreshTestCase(TracerTestCase):
    """traced_wsgi() wraps Bottle.wsgi() -- the WSGI entry point, run before routing -- so it
    emits every request's real method/path before request tracing starts. Bottle has no
    unpatch(); patch() is idempotent.
    """

    def setUp(self):
        super().setUp()
        patch()
        self.app = bottle.Bottle()

    def _trace_app(self):
        self.app.install(TracePlugin(service="bottle-app", tracer=self.tracer))
        self.app = webtest.TestApp(self.app)

    def test_microvm_run_hook_request(self):
        """No route is registered at the hook path: Bottle.wsgi() runs before routing, so it
        must fire even on a 404 -- the stronger, more general form of this check (whether the
        route matches doesn't change what gets dispatched).
        """
        self._trace_app()

        with mock.patch("ddtrace.contrib.internal.bottle.trace.core.dispatch", wraps=core.dispatch) as m:
            resp = self.app.post(REQUEST_STARTING_PATH, expect_errors=True)

        assert resp.status_int == 404
        m.assert_any_call(WebFrameworkEvents.WEB_REQUEST_STARTING.value, ("POST", REQUEST_STARTING_PATH))

    def test_other_request(self):
        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self._trace_app()

        with mock.patch("ddtrace.contrib.internal.bottle.trace.core.dispatch", wraps=core.dispatch) as m:
            resp = self.app.get("/hi/dougie")

        assert resp.status_int == 200
        m.assert_any_call(WebFrameworkEvents.WEB_REQUEST_STARTING.value, ("GET", "/hi/dougie"))
