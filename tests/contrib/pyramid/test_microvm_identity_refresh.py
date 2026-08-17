import mock

from ddtrace.internal.runtime import MICROVM_RUN_HOOK_PATH

from .utils import PyramidTestCase


class PyramidMicrovmIdentityRefreshTestCase(PyramidTestCase):
    """trace_tween() must pass method/path to maybe_refresh_identity(), so it detects the
    MicroVM /run hook without app changes.
    """

    def test_microvm_run_hook_request(self):
        """No route is registered at the hook path: the tween sits above EXCVIEW, ahead of
        route matching, so it still fires on the 404 (see test_404 in utils.py).
        """
        with mock.patch("ddtrace.contrib.internal.pyramid.trace.maybe_refresh_identity") as m:
            self.app.post(MICROVM_RUN_HOOK_PATH, status=404)

        m.assert_called_once_with("POST", MICROVM_RUN_HOOK_PATH)

    def test_other_request(self):
        with mock.patch("ddtrace.contrib.internal.pyramid.trace.maybe_refresh_identity") as m:
            self.app.get("/", status=200)

        m.assert_called_once_with("GET", "/")
