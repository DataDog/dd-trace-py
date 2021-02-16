import os
import socket
import tempfile
import threading
import time

import mock
import pytest

from ddtrace.compat import PY3
from ddtrace.compat import get_connection_response
from ddtrace.compat import httplib
from ddtrace.internal.uds import UDSHTTPConnection
from ddtrace.internal.writer import AgentWriter
from ddtrace.internal.writer import LogWriter
from ddtrace.internal.writer import _human_size
from ddtrace.span import Span
from ddtrace.vendor.six.moves import BaseHTTPServer
from ddtrace.vendor.six.moves import socketserver
from tests import AnyInt
from tests import BaseTestCase


class DummyOutput:
    def __init__(self):
        self.entries = []

    def write(self, message):
        self.entries.append(message)

    def flush(self):
        pass


class AgentWriterTests(BaseTestCase):
    N_TRACES = 11

    def test_metrics_disabled(self):
        statsd = mock.Mock()
        writer = AgentWriter(dogstatsd=statsd, report_metrics=False, hostname="asdf", port=1234)
        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )
        writer.stop()
        writer.join()

        statsd.increment.assert_not_called()
        statsd.distribution.assert_not_called()

    def test_metrics_bad_endpoint(self):
        statsd = mock.Mock()
        writer = AgentWriter(dogstatsd=statsd, report_metrics=True, hostname="asdf", port=1234)
        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )
        writer.stop()
        writer.join()

        statsd.increment.assert_has_calls(
            [
                mock.call("datadog.tracer.http.requests"),
            ]
        )
        statsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.buffer.accepted.traces", 10, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 50, tags=[]),
                mock.call("datadog.tracer.http.requests", 1, tags=[]),
                mock.call("datadog.tracer.http.errors", 1, tags=["type:err"]),
                mock.call("datadog.tracer.http.dropped.bytes", AnyInt(), tags=[]),
            ],
            any_order=True,
        )

    def test_metrics_trace_too_big(self):
        statsd = mock.Mock()
        writer = AgentWriter(dogstatsd=statsd, report_metrics=True, hostname="asdf", port=1234)
        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )
        writer.write(
            [Span(tracer=None, name="a" * 5000, trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(2 ** 10)]
        )
        writer.stop()
        writer.join()

        statsd.increment.assert_has_calls(
            [
                mock.call("datadog.tracer.http.requests"),
            ]
        )
        statsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.buffer.accepted.traces", 10, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 50, tags=[]),
                mock.call("datadog.tracer.buffer.dropped.traces", 1, tags=["reason:t_too_big"]),
                mock.call("datadog.tracer.buffer.dropped.bytes", AnyInt(), tags=["reason:t_too_big"]),
                mock.call("datadog.tracer.http.requests", 1, tags=[]),
                mock.call("datadog.tracer.http.errors", 1, tags=["type:err"]),
                mock.call("datadog.tracer.http.dropped.bytes", AnyInt(), tags=[]),
            ],
            any_order=True,
        )

    def test_metrics_multi(self):
        statsd = mock.Mock()
        writer = AgentWriter(dogstatsd=statsd, report_metrics=True, hostname="asdf", port=1234)
        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )
        writer.flush_queue()
        statsd.increment.assert_has_calls(
            [
                mock.call("datadog.tracer.http.requests"),
            ]
        )
        statsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.buffer.accepted.traces", 10, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 50, tags=[]),
                mock.call("datadog.tracer.http.requests", 1, tags=[]),
                mock.call("datadog.tracer.http.errors", 1, tags=["type:err"]),
                mock.call("datadog.tracer.http.dropped.bytes", AnyInt(), tags=[]),
            ],
            any_order=True,
        )

        statsd.reset_mock()

        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )
        writer.stop()
        writer.join()

        statsd.increment.assert_has_calls(
            [
                mock.call("datadog.tracer.http.requests"),
            ]
        )
        statsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.buffer.accepted.traces", 10, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 50, tags=[]),
                mock.call("datadog.tracer.http.requests", 1, tags=[]),
                mock.call("datadog.tracer.http.errors", 1, tags=["type:err"]),
                mock.call("datadog.tracer.http.dropped.bytes", AnyInt(), tags=[]),
            ],
            any_order=True,
        )


class LogWriterTests(BaseTestCase):
    N_TRACES = 11

    def create_writer(self):
        self.output = DummyOutput()
        writer = LogWriter(out=self.output)
        for i in range(self.N_TRACES):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(7)]
            )
        return writer


def test_humansize():
    assert _human_size(0) == "0B"
    assert _human_size(999) == "999B"
    assert _human_size(1000) == "1KB"
    assert _human_size(10000) == "10KB"
    assert _human_size(100000) == "100KB"
    assert _human_size(1000000) == "1MB"
    assert _human_size(10000000) == "10MB"
    assert _human_size(1000000000) == "1GB"


class _BaseHTTPRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    error_message_format = "%(message)s\n"
    error_content_type = "text/plain"

    @staticmethod
    def log_message(format, *args):  # noqa: A002
        pass


class _APIEndpointRequestHandlerTest(_BaseHTTPRequestHandler):
    def do_PUT(self):
        self.send_error(200, "OK")


class _TimeoutAPIEndpointRequestHandlerTest(_BaseHTTPRequestHandler):
    def do_PUT(self):
        # This server sleeps longer than our timeout
        time.sleep(5)


class _ResetAPIEndpointRequestHandlerTest(_BaseHTTPRequestHandler):
    def do_PUT(self):
        return


_HOST = "0.0.0.0"
_TIMEOUT_PORT = 8743
_RESET_PORT = _TIMEOUT_PORT + 1


class UDSHTTPServer(socketserver.UnixStreamServer, BaseHTTPServer.HTTPServer):
    def server_bind(self):
        BaseHTTPServer.HTTPServer.server_bind(self)


def _make_uds_server(path, request_handler):
    server = UDSHTTPServer(path, request_handler)
    t = threading.Thread(target=server.serve_forever)
    # Set daemon just in case something fails
    t.daemon = True
    t.start()

    # Wait for the server to start
    resp = None
    while resp != 200:
        conn = UDSHTTPConnection(server.server_address, False, _HOST, 2019)
        try:
            conn.request("PUT", path)
            resp = get_connection_response(conn).status
        finally:
            conn.close()
        time.sleep(0.01)

    return server, t


@pytest.fixture
def endpoint_uds_server():
    socket_name = tempfile.mktemp()
    server, thread = _make_uds_server(socket_name, _APIEndpointRequestHandlerTest)
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        os.unlink(socket_name)


def _make_server(port, request_handler):
    server = BaseHTTPServer.HTTPServer((_HOST, port), request_handler)
    t = threading.Thread(target=server.serve_forever)
    # Set daemon just in case something fails
    t.daemon = True
    t.start()
    return server, t


@pytest.fixture(scope="module")
def endpoint_test_timeout_server():
    server, thread = _make_server(_TIMEOUT_PORT, _TimeoutAPIEndpointRequestHandlerTest)
    try:
        yield thread
    finally:
        server.shutdown()
        thread.join()


@pytest.fixture(scope="module")
def endpoint_test_reset_server():
    server, thread = _make_server(_RESET_PORT, _ResetAPIEndpointRequestHandlerTest)
    try:
        yield thread
    finally:
        server.shutdown()
        thread.join()


def test_flush_connection_timeout_connect():
    writer = AgentWriter(_HOST, 2019)
    if PY3:
        exc_type = OSError
    else:
        exc_type = socket.error
    with pytest.raises(exc_type):
        writer._send_payload("foobar", 12)


def test_flush_connection_timeout(endpoint_test_timeout_server):
    writer = AgentWriter(_HOST, _TIMEOUT_PORT)
    with pytest.raises(socket.timeout):
        writer._send_payload("foobar", 12)


def test_flush_connection_reset(endpoint_test_reset_server):
    writer = AgentWriter(_HOST, _RESET_PORT)
    if PY3:
        exc_types = (httplib.BadStatusLine, ConnectionResetError)
    else:
        exc_types = (httplib.BadStatusLine,)
    with pytest.raises(exc_types):
        writer._send_payload("foobar", 12)


def test_flush_connection_uds(endpoint_uds_server):
    writer = AgentWriter(_HOST, 2019, uds_path=endpoint_uds_server.server_address)
    writer._send_payload("foobar", 12)
