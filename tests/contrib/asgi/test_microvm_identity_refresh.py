from asgiref.testing import ApplicationCommunicator
import mock
import pytest

from ddtrace.contrib.internal.asgi.middleware import TraceMiddleware
from ddtrace.internal import core
from ddtrace.internal.runtime import MICROVM_RUN_HOOK_PATH

from .test_asgi import basic_app


def _scope(method, path):
    return {
        "client": ("127.0.0.1", 32767),
        "headers": [],
        "method": method,
        "path": path,
        "query_string": b"",
        "scheme": "http",
        "server": ("127.0.0.1", 80),
        "type": "http",
    }


@pytest.mark.asyncio
async def test_microvm_run_hook_request(test_spans):
    """TraceMiddleware.__call__() must dispatch method/path before request tracing starts.

    Django (ASGI), FastAPI, and Starlette all share this middleware, so one patch covers all
    three.
    """
    app = TraceMiddleware(basic_app)
    instance = ApplicationCommunicator(app, _scope("POST", MICROVM_RUN_HOOK_PATH))

    with mock.patch("ddtrace.contrib.internal.asgi.middleware.core.dispatch", wraps=core.dispatch) as m:
        await instance.send_input({"type": "http.request", "body": b""})
        await instance.receive_output(1)
        await instance.receive_output(1)

    m.assert_any_call(core.WEB_REQUEST_STARTING, ("POST", MICROVM_RUN_HOOK_PATH))


@pytest.mark.asyncio
async def test_other_request(test_spans):
    app = TraceMiddleware(basic_app)
    instance = ApplicationCommunicator(app, _scope("GET", "/"))

    with mock.patch("ddtrace.contrib.internal.asgi.middleware.core.dispatch", wraps=core.dispatch) as m:
        await instance.send_input({"type": "http.request", "body": b""})
        await instance.receive_output(1)
        await instance.receive_output(1)

    m.assert_any_call(core.WEB_REQUEST_STARTING, ("GET", "/"))


@pytest.mark.asyncio
async def test_sub_app_does_not_double_refresh(test_spans):
    """A sub-mounted app's TraceMiddleware must not re-check a request its parent already saw
    (matches the existing not-is_subapp guard around route collection/distributed headers).
    """
    app = TraceMiddleware(basic_app)
    scope = _scope("POST", MICROVM_RUN_HOOK_PATH)
    # marks this as a sub-app request, per TraceMiddleware.__call__; request_spans matches
    # the shape _on_asgi_request always creates the dict with (ddtrace/_trace/trace_handlers.py)
    scope["datadog"] = {"request_spans": []}
    instance = ApplicationCommunicator(app, scope)

    with mock.patch("ddtrace.contrib.internal.asgi.middleware.core.dispatch", wraps=core.dispatch) as m:
        await instance.send_input({"type": "http.request", "body": b""})
        await instance.receive_output(1)
        await instance.receive_output(1)

    assert not any(call.args[0] == core.WEB_REQUEST_STARTING for call in m.call_args_list)
