import mock

from ddtrace.internal.runtime import MICROVM_RUN_HOOK_PATH

from . import BaseFlaskTestCase


class FlaskMicrovmIdentityRefreshTestCase(BaseFlaskTestCase):
    """patched_wsgi_app() must pass every request's method/path to maybe_refresh_identity(),
    so it detects the MicroVM /run hook without any changes to the app.

    The matching logic itself is tested in tests/tracer/runtime/test_runtime_id.py.
    """

    def test_microvm_run_hook_request(self):
        """No route is registered at the hook path: wsgi_app() runs before routing, so it
        must fire even on a 404 -- the stronger, more general form of this check (whether the
        route matches doesn't change what gets passed to maybe_refresh_identity()).
        """
        with mock.patch("ddtrace.contrib.internal.flask.patch.maybe_refresh_identity") as m:
            res = self.client.post(MICROVM_RUN_HOOK_PATH)

        self.assertEqual(res.status_code, 404)
        m.assert_called_once_with("POST", MICROVM_RUN_HOOK_PATH)

    def test_other_request(self):
        @self.app.route("/")
        def index():
            return "ok", 200

        with mock.patch("ddtrace.contrib.internal.flask.patch.maybe_refresh_identity") as m:
            res = self.client.get("/")

        self.assertEqual(res.status_code, 200)
        m.assert_called_once_with("GET", "/")
