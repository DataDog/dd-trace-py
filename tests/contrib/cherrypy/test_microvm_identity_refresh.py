import cherrypy
from cherrypy.test import helper
import mock

from ddtrace.contrib.internal.cherrypy.patch import TraceMiddleware
from ddtrace.internal import core
from tests.utils import TracerTestCase

from .web import StubApp


REQUEST_STARTING_PATH = "/web-request-starting"


class CherrypyMicrovmIdentityRefreshTestCase(TracerTestCase, helper.CPWebCase):
    """TraceTool._on_start_resource() must dispatch method/path before request tracing starts.

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
        with mock.patch("ddtrace.contrib.internal.cherrypy.patch.core.dispatch", wraps=core.dispatch) as m:
            self.getPage(REQUEST_STARTING_PATH, method="POST")

        self.assertStatus("404 Not Found")
        m.assert_any_call(core.WEB_REQUEST_STARTING, ("POST", REQUEST_STARTING_PATH))

    def test_other_request(self):
        with mock.patch("ddtrace.contrib.internal.cherrypy.patch.core.dispatch", wraps=core.dispatch) as m:
            self.getPage("/")

        self.assertStatus("200 OK")
        m.assert_any_call(core.WEB_REQUEST_STARTING, ("GET", "/"))
