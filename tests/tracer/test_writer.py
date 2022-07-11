import os
import socket
import tempfile
import threading
import time

import mock
import msgpack
import pytest
from six.moves import BaseHTTPServer
from six.moves import socketserver

from ddtrace.constants import KEEP_SPANS_RATE_KEY
from ddtrace.internal.compat import PY3
from ddtrace.internal.compat import get_connection_response
from ddtrace.internal.compat import httplib
from ddtrace.internal.encoding import MSGPACK_ENCODERS
from ddtrace.internal.uds import UDSHTTPConnection
from ddtrace.internal.writer import AgentWriter
from ddtrace.internal.writer import LogWriter
from ddtrace.internal.writer import Response
from ddtrace.internal.writer import _human_size
from ddtrace.span import Span
from tests.utils import AnyInt
from tests.utils import BaseTestCase
from tests.utils import override_env


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
        writer = AgentWriter(agent_url="http://asdf:1234", dogstatsd=statsd, report_metrics=False)
        for i in range(10):
            writer.write([Span(name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)])
        writer.stop()
        writer.join()

        statsd.increment.assert_not_called()
        statsd.distribution.assert_not_called()

    def test_metrics_bad_endpoint(self):
        statsd = mock.Mock()
        writer = AgentWriter(agent_url="http://asdf:1234", dogstatsd=statsd, report_metrics=True)
        for i in range(10):
            writer.write([Span(name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)])
        writer.stop()
        writer.join()

        statsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.buffer.accepted.traces", 10, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 50, tags=[]),
                mock.call("datadog.tracer.http.requests", writer.RETRY_ATTEMPTS, tags=[]),
                mock.call("datadog.tracer.http.errors", 1, tags=["type:err"]),
                mock.call("datadog.tracer.http.dropped.bytes", AnyInt(), tags=[]),
            ],
            any_order=True,
        )

    def test_metrics_trace_too_big(self):
        statsd = mock.Mock()
        writer = AgentWriter(agent_url="http://asdf:1234", dogstatsd=statsd, report_metrics=True)
        for i in range(10):
            writer.write([Span(name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)])
        writer.write([Span(name="a" * 5000, trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(2 ** 10)])
        writer.stop()
        writer.join()

        statsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.buffer.accepted.traces", 10, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 50, tags=[]),
                mock.call("datadog.tracer.buffer.dropped.traces", 1, tags=["reason:t_too_big"]),
                mock.call("datadog.tracer.buffer.dropped.bytes", AnyInt(), tags=["reason:t_too_big"]),
                mock.call("datadog.tracer.http.requests", writer.RETRY_ATTEMPTS, tags=[]),
                mock.call("datadog.tracer.http.errors", 1, tags=["type:err"]),
                mock.call("datadog.tracer.http.dropped.bytes", AnyInt(), tags=[]),
            ],
            any_order=True,
        )

    def test_metrics_multi(self):
        statsd = mock.Mock()
        writer = AgentWriter(agent_url="http://asdf:1234", dogstatsd=statsd, report_metrics=True)
        for i in range(10):
            writer.write([Span(name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)])
        writer.flush_queue()
        statsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.buffer.accepted.traces", 10, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 50, tags=[]),
                mock.call("datadog.tracer.http.requests", writer.RETRY_ATTEMPTS, tags=[]),
                mock.call("datadog.tracer.http.errors", 1, tags=["type:err"]),
                mock.call("datadog.tracer.http.dropped.bytes", AnyInt(), tags=[]),
            ],
            any_order=True,
        )

        statsd.reset_mock()

        for i in range(10):
            writer.write([Span(name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)])
        writer.stop()
        writer.join()

        statsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.buffer.accepted.traces", 10, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 50, tags=[]),
                mock.call("datadog.tracer.http.requests", writer.RETRY_ATTEMPTS, tags=[]),
                mock.call("datadog.tracer.http.errors", 1, tags=["type:err"]),
                mock.call("datadog.tracer.http.dropped.bytes", AnyInt(), tags=[]),
            ],
            any_order=True,
        )

    def test_write_sync(self):
        statsd = mock.Mock()
        writer = AgentWriter(agent_url="http://asdf:1234", dogstatsd=statsd, report_metrics=True, sync_mode=True)
        writer.write([Span(name="name", trace_id=1, span_id=j, parent_id=j - 1 or None) for j in range(5)])
        statsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.buffer.accepted.traces", 1, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 5, tags=[]),
                mock.call("datadog.tracer.http.requests", writer.RETRY_ATTEMPTS, tags=[]),
                mock.call("datadog.tracer.http.errors", 1, tags=["type:err"]),
                mock.call("datadog.tracer.http.dropped.bytes", AnyInt(), tags=[]),
            ],
            any_order=True,
        )

    def test_drop_reason_bad_endpoint(self):
        statsd = mock.Mock()
        writer_metrics_reset = mock.Mock()
        writer = AgentWriter(agent_url="http://asdf:1234", dogstatsd=statsd, report_metrics=False)
        writer._metrics_reset = writer_metrics_reset
        for i in range(10):
            writer.write([Span(name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)])
        writer.stop()
        writer.join()

        writer_metrics_reset.assert_called_once()

        assert 1 == writer._metrics["http.errors"]["count"]
        assert 10 == writer._metrics["http.dropped.traces"]["count"]

    def test_drop_reason_trace_too_big(self):
        statsd = mock.Mock()
        writer_metrics_reset = mock.Mock()
        writer = AgentWriter(agent_url="http://asdf:1234", dogstatsd=statsd, report_metrics=False)
        writer._metrics_reset = writer_metrics_reset
        for i in range(10):
            writer.write([Span(name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)])
        writer.write([Span(name="a" * 5000, trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(2 ** 10)])
        writer.stop()
        writer.join()

        writer_metrics_reset.assert_called_once()

        assert 1 == writer._metrics["buffer.dropped.traces"]["count"]
        assert ["reason:t_too_big"] == writer._metrics["buffer.dropped.traces"]["tags"]

    def test_drop_reason_buffer_full(self):
        statsd = mock.Mock()
        writer_metrics_reset = mock.Mock()
        writer = AgentWriter(agent_url="http://asdf:1234", buffer_size=5235, dogstatsd=statsd, report_metrics=False)
        writer._metrics_reset = writer_metrics_reset
        for i in range(10):
            writer.write([Span(name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)])
        writer.write([Span(name="a", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)])
        writer.stop()
        writer.join()

        writer_metrics_reset.assert_called_once()

        assert 1 == writer._metrics["buffer.dropped.traces"]["count"]
        assert ["reason:full"] == writer._metrics["buffer.dropped.traces"]["tags"]

    def test_drop_reason_encoding_error(self):
        n_traces = 10
        statsd = mock.Mock()
        writer_encoder = mock.Mock()
        writer_encoder.__len__ = (lambda *args: n_traces).__get__(writer_encoder)
        writer_metrics_reset = mock.Mock()
        writer_encoder.encode.side_effect = Exception
        writer = AgentWriter(agent_url="http://asdf:1234", dogstatsd=statsd, report_metrics=False)
        writer._encoder = writer_encoder
        writer._metrics_reset = writer_metrics_reset
        for i in range(n_traces):
            writer.write([Span(name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)])

        writer.stop()
        writer.join()

        writer_metrics_reset.assert_called_once()

        assert 10 == writer._metrics["encoder.dropped.traces"]["count"]

    def test_keep_rate(self):
        statsd = mock.Mock()
        writer_run_periodic = mock.Mock()
        writer_put = mock.Mock()
        writer_put.return_value = Response(status=200)
        writer = AgentWriter(agent_url="http://asdf:1234", dogstatsd=statsd, report_metrics=False)
        writer.run_periodic = writer_run_periodic
        writer._put = writer_put

        traces = [
            [Span(name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)] for i in range(4)
        ]

        traces_too_big = [
            [Span(name="a" * 5000, trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(2 ** 10)]
            for i in range(4)
        ]

        # 1. We write 4 traces successfully.
        for trace in traces:
            writer.write(trace)
        writer.flush_queue()

        payload = msgpack.unpackb(writer_put.call_args.args[0])
        # No previous drops.
        assert 0.0 == writer._drop_sma.get()
        # 4 traces written.
        assert 4 == len(payload)
        # 100% of traces kept (refers to the past).
        # No traces sent before now so 100% kept.
        for trace in payload:
            assert 1.0 == trace[0]["metrics"].get(KEEP_SPANS_RATE_KEY, -1)

        # 2. We fail to write 4 traces because of size limitation.
        for trace in traces_too_big:
            writer.write(trace)
        writer.flush_queue()

        # 50% of traces were dropped historically.
        # 4 successfully written before and 4 dropped now.
        assert 0.5 == writer._drop_sma.get()
        # put not called since no new traces are available.
        writer_put.assert_called_once()

        # 3. We write 2 traces successfully.
        for trace in traces[:2]:
            writer.write(trace)
        writer.flush_queue()

        payload = msgpack.unpackb(writer_put.call_args.args[0])
        # 40% of traces were dropped historically.
        assert 0.4 == writer._drop_sma.get()
        # 2 traces written.
        assert 2 == len(payload)
        # 50% of traces kept (refers to the past).
        # We had 4 successfully written and 4 dropped.
        for trace in payload:
            assert 0.5 == trace[0]["metrics"].get(KEEP_SPANS_RATE_KEY, -1)

        # 4. We write 1 trace successfully and fail to write 3.
        writer.write(traces[0])
        for trace in traces_too_big[:3]:
            writer.write(trace)
        writer.flush_queue()

        payload = msgpack.unpackb(writer_put.call_args.args[0])
        # 50% of traces were dropped historically.
        assert 0.5 == writer._drop_sma.get()
        # 1 trace written.
        assert 1 == len(payload)
        # 60% of traces kept (refers to the past).
        # We had 4 successfully written, then 4 dropped, then 2 written.
        for trace in payload:
            assert 0.6 == trace[0]["metrics"].get(KEEP_SPANS_RATE_KEY, -1)


class LogWriterTests(BaseTestCase):
    N_TRACES = 11

    def create_writer(self):
        self.output = DummyOutput()
        writer = LogWriter(out=self.output)
        for i in range(self.N_TRACES):
            writer.write([Span(name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(7)])
        return writer

    def test_log_writer(self):
        self.create_writer()
        self.assertEqual(len(self.output.entries), self.N_TRACES)


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

    expected_path_prefix = None

    def do_PUT(self):
        if self.expected_path_prefix is not None:
            assert self.path.startswith(self.expected_path_prefix)
        self.send_error(200, "OK")


class _TimeoutAPIEndpointRequestHandlerTest(_BaseHTTPRequestHandler):
    def do_PUT(self):
        # This server sleeps longer than our timeout
        time.sleep(5)


class _ResetAPIEndpointRequestHandlerTest(_BaseHTTPRequestHandler):
    def do_PUT(self):
        return


_HOST = "0.0.0.0"
_PORT = 8743
_TIMEOUT_PORT = _PORT + 1
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
        conn = UDSHTTPConnection(server.server_address, _HOST, 2019)
        try:
            conn.request("PUT", "/")
            resp = get_connection_response(conn).status
        finally:
            conn.close()
        time.sleep(0.01)

    return server, t


@pytest.fixture
def endpoint_uds_server():
    socket_name = tempfile.mktemp()
    handler = _APIEndpointRequestHandlerTest
    server, thread = _make_uds_server(socket_name, handler)
    handler.expected_path_prefix = "/v0."
    try:
        yield server
    finally:
        handler.expected_path_prefix = None
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


@pytest.fixture
def endpoint_assert_path():
    handler = _APIEndpointRequestHandlerTest
    server, thread = _make_server(_PORT, handler)

    def configure(expected_path_prefix=None):
        handler.expected_path_prefix = expected_path_prefix
        return thread

    try:
        yield configure
    finally:
        handler.expected_path_prefix = None
        server.shutdown()
        thread.join()


def test_agent_url_path(endpoint_assert_path):
    # test without base path
    endpoint_assert_path("/v0.")
    writer = AgentWriter(agent_url="http://%s:%s/" % (_HOST, _PORT))
    writer._encoder.put([Span("foobar")])
    writer.flush_queue(raise_exc=True)

    # test without base path nor trailing slash
    writer = AgentWriter(agent_url="http://%s:%s" % (_HOST, _PORT))
    writer._encoder.put([Span("foobar")])
    writer.flush_queue(raise_exc=True)

    # test with a base path
    endpoint_assert_path("/test/v0.")
    writer = AgentWriter(agent_url="http://%s:%s/test/" % (_HOST, _PORT))
    writer._encoder.put([Span("foobar")])
    writer.flush_queue(raise_exc=True)


def test_flush_connection_timeout_connect():
    writer = AgentWriter(agent_url="http://%s:%s" % (_HOST, 2019))
    if PY3:
        exc_type = OSError
    else:
        exc_type = socket.error
    with pytest.raises(exc_type):
        writer._encoder.put([Span("foobar")])
        writer.flush_queue(raise_exc=True)


def test_flush_connection_timeout(endpoint_test_timeout_server):
    writer = AgentWriter(agent_url="http://%s:%s" % (_HOST, _TIMEOUT_PORT))
    with pytest.raises(socket.timeout):
        writer._encoder.put([Span("foobar")])
        writer.flush_queue(raise_exc=True)


def test_flush_connection_reset(endpoint_test_reset_server):
    writer = AgentWriter(agent_url="http://%s:%s" % (_HOST, _RESET_PORT))
    if PY3:
        exc_types = (httplib.BadStatusLine, ConnectionResetError)
    else:
        exc_types = (httplib.BadStatusLine,)
    with pytest.raises(exc_types):
        writer._encoder.put([Span("foobar")])
        writer.flush_queue(raise_exc=True)


def test_flush_connection_uds(endpoint_uds_server):
    writer = AgentWriter(agent_url="unix://%s" % endpoint_uds_server.server_address)
    writer._encoder.put([Span("foobar")])
    writer.flush_queue(raise_exc=True)


def test_flush_queue_raise():
    writer = AgentWriter(agent_url="http://dne:1234")

    # Should not raise
    writer.write([])
    writer.flush_queue(raise_exc=False)

    error = OSError if PY3 else IOError
    with pytest.raises(error):
        writer.write([])
        writer.flush_queue(raise_exc=True)


def test_racing_start():
    writer = AgentWriter(agent_url="http://dne:1234")

    def do_write(i):
        writer.write([Span(str(i))])

    ts = [threading.Thread(target=do_write, args=(i,)) for i in range(100)]
    for t in ts:
        t.start()

    for t in ts:
        t.join()

    assert len(writer._encoder) == 100


def test_additional_headers():
    with override_env(dict(_DD_TRACE_WRITER_ADDITIONAL_HEADERS="additional-header:additional-value,header2:value2")):
        writer = AgentWriter(agent_url="http://localhost:9126")
        assert writer._headers["additional-header"] == "additional-value"
        assert writer._headers["header2"] == "value2"


def test_bad_encoding(monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", "foo")

    with pytest.raises(ValueError):
        AgentWriter(agent_url="http://localhost:9126")


@pytest.mark.parametrize(
    "init_api_version,api_version,endpoint,encoder_cls",
    [
        (None, "v0.3", "v0.3/traces", MSGPACK_ENCODERS["v0.3"]),
        ("v0.3", "v0.3", "v0.3/traces", MSGPACK_ENCODERS["v0.3"]),
        ("v0.4", "v0.4", "v0.4/traces", MSGPACK_ENCODERS["v0.4"]),
        ("v0.5", "v0.5", "v0.5/traces", MSGPACK_ENCODERS["v0.5"]),
    ],
)
def test_writer_recreate_api_version(init_api_version, api_version, endpoint, encoder_cls):
    writer = AgentWriter(agent_url="http://dne:1234", api_version=init_api_version)
    assert writer._api_version == api_version
    assert writer._endpoint == endpoint
    assert isinstance(writer._encoder, encoder_cls)

    writer = writer.recreate()
    assert writer._api_version == api_version
    assert writer._endpoint == endpoint
    assert isinstance(writer._encoder, encoder_cls)


def test_writer_reuse_connections_envvar(monkeypatch):
    monkeypatch.setenv("DD_TRACE_WRITER_REUSE_CONNECTIONS", "false")
    writer = AgentWriter(agent_url="http://localhost:9126")
    assert not writer._reuse_connections

    monkeypatch.setenv("DD_TRACE_WRITER_REUSE_CONNECTIONS", "true")
    writer = AgentWriter(agent_url="http://localhost:9126")
    assert writer._reuse_connections


def test_writer_reuse_connections():
    # Ensure connection is not reused
    writer = AgentWriter(agent_url="http://localhost:9126", reuse_connections=True)
    # Do an initial flush to get a connection
    writer.flush_queue()
    assert writer._conn is None
    writer.flush_queue()
    assert writer._conn is None


def test_writer_reuse_connections_false():
    # Ensure connection is reused
    writer = AgentWriter(agent_url="http://localhost:9126", reuse_connections=False)
    # Do an initial flush to get a connection
    writer.flush_queue()
    conn = writer._conn
    # And another to potentially have it reset
    writer.flush_queue()
    assert writer._conn is conn
