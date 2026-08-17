import cherrypy
from cherrypy.test import helper
import mock

from ddtrace.contrib.internal.cherrypy.patch import TraceMiddleware
from ddtrace.internal.runtime import MICROVM_RUN_HOOK_PATH
from tests.utils import TracerTestCase

from .web import StubApp


class CherrypyMicrovmIdentityRefreshTestCase(TracerTestCase, helper.CPWebCase):
    """TraceTool._on_start_resource() must pass method/path to maybe_refresh_identity().

    CherryPy has no automatic patch() -- this only fires once the app has wrapped itself in
    TraceMiddleware, unlike the auto-instrumented frameworks.
    """

    @staticmethod
    def setup_server():
        cherrypy.tree.mount(
            StubApp(),
            "/",
            {
                "/": {"tools.tracer.on": True},
            },
        )

    def setUp(self):
        super(CherrypyMicrovmIdentityRefreshTestCase, self).setUp()
        self.traced_app = TraceMiddleware(cherrypy, service="test.cherrypy.service")

    def test_microvm_run_hook_request(self):
        """No handler is registered at the hook path: _on_start_resource() still fires on the
        404 (see test_404 in test_middleware.py).
        """
        with mock.patch("ddtrace.contrib.internal.cherrypy.patch.maybe_refresh_identity") as m:
            self.getPage(MICROVM_RUN_HOOK_PATH, method="POST")

        self.assertStatus("404 Not Found")
        m.assert_called_once_with("POST", MICROVM_RUN_HOOK_PATH)

    def test_other_request(self):
        with mock.patch("ddtrace.contrib.internal.cherrypy.patch.maybe_refresh_identity") as m:
            self.getPage("/")

        self.assertStatus("200 OK")
        m.assert_called_once_with("GET", "/")
