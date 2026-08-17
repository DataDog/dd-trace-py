import bottle
import mock
import webtest

from ddtrace.contrib.internal.bottle.patch import TracePlugin
from ddtrace.internal.runtime import MICROVM_RUN_HOOK_PATH
from tests.utils import TracerTestCase


class BottleMicrovmIdentityRefreshTestCase(TracerTestCase):
    """TracePlugin's wrapped callback must pass method/route to maybe_refresh_identity().

    Unlike every other framework here, Bottle only invokes this per matched route, so an
    unregistered hook path would NOT be detected -- see docs/aws-lambda-microvm-identity-refresh.md.
    """

    def setUp(self):
        super().setUp()
        self.app = bottle.Bottle()

    def _trace_app(self):
        self.app.install(TracePlugin(service="bottle-app", tracer=self.tracer))
        self.app = webtest.TestApp(self.app)

    def test_microvm_run_hook_request(self):
        @self.app.route(MICROVM_RUN_HOOK_PATH, method="POST")
        def run_hook():
            return ""

        self._trace_app()

        with mock.patch("ddtrace.contrib.internal.bottle.trace.maybe_refresh_identity") as m:
            resp = self.app.post(MICROVM_RUN_HOOK_PATH)

        assert resp.status_int == 200
        m.assert_called_once_with("POST", MICROVM_RUN_HOOK_PATH)

    def test_other_request(self):
        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self._trace_app()

        with mock.patch("ddtrace.contrib.internal.bottle.trace.maybe_refresh_identity") as m:
            resp = self.app.get("/hi/dougie")

        assert resp.status_int == 200
        m.assert_called_once_with("GET", "/hi/<name>")
