import mock

from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.contrib.internal.flask.patch import patched_wsgi_app
from ddtrace.internal import core
import ddtrace.internal.runtime as runtime

from . import BaseFlaskTestCase


class FlaskMicrovmIdentityRefreshTestCase(BaseFlaskTestCase):
    """patched_wsgi_app() must dispatch MicroVM requests before tracing starts.

    The matching logic itself is tested in tests/tracer/runtime/test_runtime_id.py.
    """

    def test_microvm_run_hook_request(self):
        """No route is registered at the hook path: wsgi_app() runs before routing, so it
        must fire even on a 404 -- the stronger, more general form of this check (whether the
        route matches doesn't change what gets dispatched).
        """
        with (
            mock.patch("ddtrace.contrib.internal.flask.patch.in_aws_lambda_microvm", return_value=True),
            mock.patch("ddtrace.contrib.internal.flask.patch.core.dispatch", wraps=core.dispatch) as m,
        ):
            res = self.client.post(runtime.MICROVM_RUN_HOOK_PATH)

        self.assertEqual(res.status_code, 404)
        m.assert_any_call(WebFrameworkEvents.WEB_REQUEST_STARTING.value, ("POST", runtime.MICROVM_RUN_HOOK_PATH))

    def test_microvm_run_hook_refreshes_identity(self):
        event_name = WebFrameworkEvents.WEB_REQUEST_STARTING.value
        core.reset_listeners(event_name, runtime.maybe_refresh_identity)
        runtime._IDENTITY_REFRESH_HOOK_REFRESHED.clear()

        try:
            with (
                mock.patch("ddtrace.contrib.internal.flask.patch.in_aws_lambda_microvm", return_value=True),
                mock.patch("ddtrace.internal.runtime.in_aws_lambda_microvm", return_value=True),
            ):
                runtime.listen_for_identity_refresh_hooks(core.on)

                runtime_id = runtime.get_runtime_id()

                res = self.client.post(runtime.MICROVM_RUN_HOOK_PATH)

                self.assertEqual(res.status_code, 404)
                refreshed_runtime_id = runtime.get_runtime_id()
                self.assertNotEqual(refreshed_runtime_id, runtime_id)

                res = self.client.post(runtime.MICROVM_RUN_HOOK_PATH)

                self.assertEqual(res.status_code, 404)
                self.assertEqual(runtime.get_runtime_id(), refreshed_runtime_id)
        finally:
            core.reset_listeners(event_name, runtime.maybe_refresh_identity)
            runtime._IDENTITY_REFRESH_HOOK_REFRESHED.clear()

    def test_other_request_does_not_dispatch_outside_microvm(self):
        @self.app.route("/")
        def index():
            return "ok", 200

        with (
            mock.patch("ddtrace.contrib.internal.flask.patch.in_aws_lambda_microvm", return_value=False),
            mock.patch("ddtrace.contrib.internal.flask.patch.core.dispatch", wraps=core.dispatch) as m,
        ):
            res = self.client.get("/")

        self.assertEqual(res.status_code, 200)
        assert all(call.args[0] != WebFrameworkEvents.WEB_REQUEST_STARTING.value for call in m.call_args_list)

    def test_dispatches_script_name_prefixed_request_path(self):
        script_name, hook_name = runtime.MICROVM_RUN_HOOK_PATH.rsplit("/", 1)
        path_info = "/" + hook_name

        with (
            mock.patch("ddtrace.contrib.internal.flask.patch.in_aws_lambda_microvm", return_value=True),
            mock.patch("ddtrace.contrib.internal.flask.patch.core.dispatch", wraps=core.dispatch) as m,
        ):
            res = self.client.post(path_info, environ_overrides={"SCRIPT_NAME": script_name})

        self.assertEqual(res.status_code, 404)
        m.assert_any_call(
            WebFrameworkEvents.WEB_REQUEST_STARTING.value,
            (runtime.MICROVM_RUN_HOOK_METHOD, runtime.MICROVM_RUN_HOOK_PATH),
        )

    def test_pre_request_event_dispatches_before_wsgi_middleware(self):
        events = []
        environ = {"REQUEST_METHOD": "POST", "PATH_INFO": runtime.MICROVM_RUN_HOOK_PATH, "SCRIPT_NAME": ""}

        def start_response(status, headers, exc_info=None):
            pass

        def wrapped(environ, start_response):
            return []

        def dispatch(name, args):
            if name == WebFrameworkEvents.WEB_REQUEST_STARTING.value:
                events.append("starting")

        class WSGIMiddleware:
            def __init__(self, app, tracer, integration_config):
                pass

            def __call__(self, environ, start_response):
                events.append("middleware")
                return []

        with (
            mock.patch("ddtrace.contrib.internal.flask.patch.in_aws_lambda_microvm", return_value=True),
            mock.patch("ddtrace.contrib.internal.flask.patch.core.dispatch", side_effect=dispatch),
            mock.patch("ddtrace.contrib.internal.flask.patch._collect_routes_once"),
            mock.patch("ddtrace.contrib.internal.flask.patch.is_tracing_enabled", return_value=True),
            mock.patch("ddtrace.contrib.internal.flask.patch._FlaskWSGIMiddleware", WSGIMiddleware),
        ):
            patched_wsgi_app(wrapped, self.app, (environ, start_response), {})

        assert events == ["starting", "middleware"]
