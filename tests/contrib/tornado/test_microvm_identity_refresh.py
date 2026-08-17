import mock

from ddtrace.internal.runtime import MICROVM_RUN_HOOK_PATH

from .utils import TornadoTestCase


class TornadoMicrovmIdentityRefreshTestCase(TornadoTestCase):
    """execute() (wrapping RequestHandler._execute) must pass method/path to
    maybe_refresh_identity(), so it detects the MicroVM /run hook without app changes.
    """

    def test_microvm_run_hook_request(self):
        """No handler is registered at the hook path: unmatched routes fall back to Tornado's
        ErrorHandler, itself a RequestHandler, so execute() still fires (see test_404_handler
        in test_tornado_web.py).
        """
        with mock.patch("ddtrace.contrib.internal.tornado.handlers.maybe_refresh_identity") as m:
            response = self.fetch(MICROVM_RUN_HOOK_PATH, method="POST", body="")

        self.assertEqual(response.code, 404)
        m.assert_called_once_with("POST", MICROVM_RUN_HOOK_PATH)

    def test_other_request(self):
        with mock.patch("ddtrace.contrib.internal.tornado.handlers.maybe_refresh_identity") as m:
            response = self.fetch("/success/")

        self.assertEqual(response.code, 200)
        m.assert_called_once_with("GET", "/success/")
