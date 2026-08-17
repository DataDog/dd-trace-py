from asgiref.testing import ApplicationCommunicator
import mock
import pytest

from ddtrace.contrib.internal.asgi.middleware import TraceMiddleware
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
    """TraceMiddleware.__call__() must pass method/path to maybe_refresh_identity(), so it
    detects the MicroVM /run hook without app changes. Django (ASGI), FastAPI, and Starlette
    all share this middleware, so one patch covers all three.
    """
    app = TraceMiddleware(basic_app)
    instance = ApplicationCommunicator(app, _scope("POST", MICROVM_RUN_HOOK_PATH))

    with mock.patch("ddtrace.contrib.internal.asgi.middleware.maybe_refresh_identity") as m:
        await instance.send_input({"type": "http.request", "body": b""})
        await instance.receive_output(1)
        await instance.receive_output(1)

    m.assert_called_once_with("POST", MICROVM_RUN_HOOK_PATH)


@pytest.mark.asyncio
async def test_other_request(test_spans):
    app = TraceMiddleware(basic_app)
    instance = ApplicationCommunicator(app, _scope("GET", "/"))

    with mock.patch("ddtrace.contrib.internal.asgi.middleware.maybe_refresh_identity") as m:
        await instance.send_input({"type": "http.request", "body": b""})
        await instance.receive_output(1)
        await instance.receive_output(1)

    m.assert_called_once_with("GET", "/")


@pytest.mark.asyncio
async def test_sub_app_does_not_double_refresh(test_spans):
    """A sub-mounted app's TraceMiddleware must not re-check a request its parent already saw
    (matches the existing not-is_subapp guard around route collection/distributed headers).
    """
    app = TraceMiddleware(basic_app)
    scope = _scope("POST", MICROVM_RUN_HOOK_PATH)
    scope["datadog"] = {}  # marks this as a sub-app request, per TraceMiddleware.__call__
    instance = ApplicationCommunicator(app, scope)

    with mock.patch("ddtrace.contrib.internal.asgi.middleware.maybe_refresh_identity") as m:
        await instance.send_input({"type": "http.request", "body": b""})
        await instance.receive_output(1)
        await instance.receive_output(1)

    m.assert_not_called()
