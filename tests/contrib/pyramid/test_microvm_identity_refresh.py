import mock

from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.contrib.internal.pyramid.constants import SETTINGS_TRACE_ENABLED
from ddtrace.internal import core

from .utils import PyramidTestCase


REQUEST_STARTING_PATH = "/web-request-starting"


class PyramidMicrovmIdentityRefreshTestCase(PyramidTestCase):
    """trace_tween() must dispatch method/path before request tracing starts."""

    def test_microvm_run_hook_request(self):
        """No route is registered at the hook path: the tween sits above EXCVIEW, ahead of
        route matching, so it still fires on the 404 (see test_404 in utils.py).
        """
        with mock.patch("ddtrace.contrib.internal.pyramid.trace.core.dispatch", wraps=core.dispatch) as m:
            self.app.post(REQUEST_STARTING_PATH, status=404)

        m.assert_any_call(WebFrameworkEvents.WEB_REQUEST_STARTING.value, ("POST", REQUEST_STARTING_PATH))

    def test_other_request(self):
        with mock.patch("ddtrace.contrib.internal.pyramid.trace.core.dispatch", wraps=core.dispatch) as m:
            self.app.get("/", status=200)

        m.assert_any_call(WebFrameworkEvents.WEB_REQUEST_STARTING.value, ("GET", "/"))

    def test_microvm_run_hook_request_with_tracing_disabled(self):
        """Identity refresh is a process-wide concern, not a tracing concern: it must still
        fire even when the app has tracing disabled, which used to skip installing the tween
        entirely and return the undecorated handler.
        """
        self.override_settings({"datadog_trace_service": "foobar", SETTINGS_TRACE_ENABLED: "false"})

        with mock.patch("ddtrace.contrib.internal.pyramid.trace.core.dispatch", wraps=core.dispatch) as m:
            self.app.post(REQUEST_STARTING_PATH, status=404)

        m.assert_any_call(WebFrameworkEvents.WEB_REQUEST_STARTING.value, ("POST", REQUEST_STARTING_PATH))
        assert len(self.pop_spans()) == 0
