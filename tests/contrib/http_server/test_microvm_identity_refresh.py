import http.server
import io

import mock
import pytest

from ddtrace.contrib.internal.http_server.patch import patch
from ddtrace.contrib.internal.http_server.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal.runtime import MICROVM_RUN_HOOK_PATH


def _handler_for(method, path):
    """Build a BaseHTTPRequestHandler with just enough state for parse_request() to run,
    bypassing the real socket/handle() loop (which would also dispatch to do_GET/do_POST).
    """
    handler = http.server.BaseHTTPRequestHandler.__new__(http.server.BaseHTTPRequestHandler)
    handler.raw_requestline = f"{method} {path} HTTP/1.1\r\n".encode()
    handler.rfile = io.BytesIO(b"Host: localhost\r\n\r\n")
    return handler


@pytest.fixture(autouse=True)
def _patched():
    patch()
    yield
    unpatch()


def test_microvm_run_hook_request():
    """parse_request() must dispatch method/path before request tracing starts.

    This covers apps that implement the hook with a raw http.server handler instead of a
    supported web framework.
    """
    with mock.patch("ddtrace.contrib.internal.http_server.patch.core.dispatch", wraps=core.dispatch) as m:
        parsed = _handler_for("POST", MICROVM_RUN_HOOK_PATH).parse_request()

    assert parsed is True
    m.assert_any_call(core.WEB_REQUEST_STARTING, ("POST", MICROVM_RUN_HOOK_PATH))


def test_other_request():
    with mock.patch("ddtrace.contrib.internal.http_server.patch.core.dispatch", wraps=core.dispatch) as m:
        parsed = _handler_for("GET", "/").parse_request()

    assert parsed is True
    m.assert_any_call(core.WEB_REQUEST_STARTING, ("GET", "/"))


def test_malformed_request_does_not_refresh():
    """A request line parse_request() can't parse must not emit the pre-request event.

    There is no method/path to report.
    """
    handler = http.server.BaseHTTPRequestHandler.__new__(http.server.BaseHTTPRequestHandler)
    handler.raw_requestline = b""
    handler.rfile = io.BytesIO(b"")

    with mock.patch("ddtrace.contrib.internal.http_server.patch.core.dispatch", wraps=core.dispatch) as m:
        parsed = handler.parse_request()

    assert parsed is False
    assert not any(call.args[0] == core.WEB_REQUEST_STARTING for call in m.call_args_list)
