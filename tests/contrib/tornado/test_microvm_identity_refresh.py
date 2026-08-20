import mock

from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.internal import core

from .utils import TornadoTestCase


REQUEST_STARTING_PATH = "/web-request-starting"


class TornadoMicrovmIdentityRefreshTestCase(TornadoTestCase):
    """execute() must dispatch method/path before request tracing starts."""

    def test_microvm_run_hook_request(self):
        """No handler is registered at the hook path: unmatched routes fall back to Tornado's
        ErrorHandler, itself a RequestHandler, so execute() still fires (see test_404_handler
        in test_tornado_web.py).
        """
        with mock.patch("ddtrace.contrib.internal.tornado.handlers.core.dispatch", wraps=core.dispatch) as m:
            response = self.fetch(REQUEST_STARTING_PATH, method="POST", body="")

        self.assertEqual(response.code, 404)
        m.assert_any_call(WebFrameworkEvents.WEB_REQUEST_STARTING.value, ("POST", REQUEST_STARTING_PATH))

    def test_other_request(self):
        with mock.patch("ddtrace.contrib.internal.tornado.handlers.core.dispatch", wraps=core.dispatch) as m:
            response = self.fetch("/success/")

        self.assertEqual(response.code, 200)
        m.assert_any_call(WebFrameworkEvents.WEB_REQUEST_STARTING.value, ("GET", "/success/"))
