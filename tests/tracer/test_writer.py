import contextlib
import http.client as httplib
import http.server
import os
import socket
import socketserver
import sys
import tempfile
import threading
import time

import mock
import msgpack
import pytest

import ddtrace
from ddtrace import config
from ddtrace.constants import _KEEP_SPANS_RATE_KEY
from ddtrace.internal.ci_visibility.writer import CIVisibilityWriter
from ddtrace.internal.encoding import MSGPACK_ENCODERS
from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.uds import UDSHTTPConnection
from ddtrace.internal.writer import AgentWriter
from ddtrace.internal.writer import LogWriter
from ddtrace.internal.writer import Response
from ddtrace.internal.writer import _human_size
from ddtrace.trace import Span
from tests.utils import AnyInt
from tests.utils import BaseTestCase
from tests.utils import override_env
from tests.utils import override_global_config


@contextlib.contextmanager
def mock_sys_platform(new_value):
    old_value = sys.platform
    try:
        sys.platform = new_value
        yield
    finally:
        sys.platform = old_value


class DummyOutput:
    def __init__(self):
        self.entries = []

    def write(self, message):
        self.entries.append(message)

    def flush(self):
        pass


class AgentWriterTests(BaseTestCase):
    N_TRACES = 11
    WRITER_CLASS = AgentWriter

    def test_metrics_disabled(self):
        statsd = mock.Mock()
        with override_global_config(dict(_health_metrics_enabled=False)):
            writer = self.WRITER_CLASS("http://asdf:1234", dogstatsd=statsd)
            for i in range(10):
                writer.write([Span(name="name", trace_id=i, span_id=j + 1, parent_id=j or None) for j in range(5)])
            writer.stop()
            writer.join()

        statsd.increment.assert_not_called()
        statsd.distribution.assert_not_called()

    def test_metrics_bad_endpoint(self):
        statsd = mock.Mock()
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = self.WRITER_CLASS("http://asdf:1234", dogstatsd=statsd, sync_mode=False)
            for i in range(10):
                writer.write([Span(name="name", trace_id=i, span_id=j + 1, parent_id=j or None) for j in range(5)])
            writer.stop()
            writer.join()

        statsd.distribution.assert_has_calls(
            [mock.call("datadog.%s.buffer.accepted.traces" % writer.STATSD_NAMESPACE, 1, tags=None)] * 10
            + [mock.call("datadog.%s.buffer.accepted.spans" % writer.STATSD_NAMESPACE, 5, tags=None)] * 10
            + [mock.call("datadog.%s.http.requests" % writer.STATSD_NAMESPACE, 1, tags=None)] * writer.RETRY_ATTEMPTS
            + [mock.call("datadog.%s.http.errors" % writer.STATSD_NAMESPACE, 1, tags=["type:err"])]
            + [mock.call("datadog.%s.http.dropped.bytes" % writer.STATSD_NAMESPACE, AnyInt(), tags=None)],
            any_order=True,
        )

    def test_metrics_trace_too_big(self):
        statsd = mock.Mock()
        with override_global_config(dict(_health_metrics_enabled=True, _trace_writer_buffer_size=15000)):
            writer = self.WRITER_CLASS("http://asdf:1234", dogstatsd=statsd)
            for i in range(10):
                writer.write([Span(name="name", trace_id=i, span_id=j + 1, parent_id=j or None) for j in range(5)])

            massive_trace = []
            for i in range(10):
                span = Span("mmon", "mmon" + str(i), "mmon" + str(i))
                for j in range(50):
                    key = "opqr012|~" + str(i) + str(j)
                    val = "stuv345!@#" + str(i) + str(j)
                    span.set_tag_str(key, val)
                massive_trace.append(span)

            writer.write(massive_trace)
            writer.stop()
            writer.join()

        statsd.distribution.assert_has_calls(
            [mock.call("datadog.%s.buffer.accepted.traces" % writer.STATSD_NAMESPACE, 1, tags=None)] * 10
            + [mock.call("datadog.%s.buffer.accepted.spans" % writer.STATSD_NAMESPACE, 5, tags=None)] * 10
            + [mock.call("datadog.%s.buffer.dropped.traces" % writer.STATSD_NAMESPACE, 1, tags=["reason:t_too_big"])]
            + [
                mock.call(
                    "datadog.%s.buffer.dropped.bytes" % writer.STATSD_NAMESPACE, AnyInt(), tags=["reason:t_too_big"]
                )
            ]
            + [mock.call("datadog.%s.http.requests" % writer.STATSD_NAMESPACE, 1, tags=None)] * writer.RETRY_ATTEMPTS
            + [mock.call("datadog.%s.http.errors" % writer.STATSD_NAMESPACE, 1, tags=["type:err"])]
            + [mock.call("datadog.%s.http.dropped.bytes" % writer.STATSD_NAMESPACE, AnyInt(), tags=None)],
            any_order=True,
        )

    def test_metrics_multi(self):
        statsd = mock.Mock()
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = self.WRITER_CLASS("http://asdf:1234", dogstatsd=statsd, sync_mode=False)
            for i in range(10):
                writer.write([Span(name="name", trace_id=i, span_id=j + 1, parent_id=j) for j in range(5)])
            writer.flush_queue()
            statsd.distribution.assert_has_calls(
                [mock.call("datadog.%s.buffer.accepted.traces" % writer.STATSD_NAMESPACE, 1, tags=None)] * 10
                + [mock.call("datadog.%s.buffer.accepted.spans" % writer.STATSD_NAMESPACE, 5, tags=None)] * 10
                + [mock.call("datadog.%s.http.requests" % writer.STATSD_NAMESPACE, 1, tags=None)]
                * writer.RETRY_ATTEMPTS
                + [mock.call("datadog.%s.http.errors" % writer.STATSD_NAMESPACE, 1, tags=["type:err"])]
                + [mock.call("datadog.%s.http.dropped.bytes" % writer.STATSD_NAMESPACE, AnyInt(), tags=None)],
                any_order=True,
            )

            statsd.reset_mock()

            for i in range(10):
                writer.write([Span(name="name", trace_id=i, span_id=j + 1, parent_id=j) for j in range(5)])
            writer.stop()
            writer.join()

            statsd.distribution.assert_has_calls(
                [mock.call("datadog.%s.buffer.accepted.traces" % writer.STATSD_NAMESPACE, 1, tags=None)] * 10
                + [mock.call("datadog.%s.buffer.accepted.spans" % writer.STATSD_NAMESPACE, 5, tags=None)] * 10
                + [mock.call("datadog.%s.http.requests" % writer.STATSD_NAMESPACE, 1, tags=None)]
                * writer.RETRY_ATTEMPTS
                + [mock.call("datadog.%s.http.errors" % writer.STATSD_NAMESPACE, 1, tags=["type:err"])]
                + [mock.call("datadog.%s.http.dropped.bytes" % writer.STATSD_NAMESPACE, AnyInt(), tags=None)],
                any_order=True,
            )

    def test_generate_health_metrics_with_different_tags(self):
        statsd = mock.Mock()
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = self.WRITER_CLASS("http://asdf:1234", dogstatsd=statsd, sync_mode=False)

            # Queue 3 health metrics where each metric has the same name but different tags
            writer.write([Span(name="name", trace_id=1, span_id=1, parent_id=None)])
            writer._metrics_dist("test_trace.queued", 1, ["k1:v1"])
            writer.write([Span(name="name", trace_id=2, span_id=2, parent_id=None)])
            writer._metrics_dist(
                "test_trace.queued",
                1,
                [
                    "k2:v2",
                    "k22:v22",
                ],
            )
            writer.write([Span(name="name", trace_id=3, span_id=3, parent_id=None)])
            writer._metrics_dist("test_trace.queued", 1)

            writer.flush_queue()
            # Ensure the health metrics are submitted with the expected tags
            statsd.distribution.assert_has_calls(
                [
                    mock.call("datadog.%s.test_trace.queued" % writer.STATSD_NAMESPACE, 1, tags=["k1:v1"]),
                    mock.call("datadog.%s.test_trace.queued" % writer.STATSD_NAMESPACE, 1, tags=["k2:v2", "k22:v22"]),
                    mock.call("datadog.%s.test_trace.queued" % writer.STATSD_NAMESPACE, 1, tags=None),
                ],
                any_order=True,
            )

    def test_report_metrics_disabled(self):
        statsd = mock.Mock()
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = self.WRITER_CLASS("http://asdf:1234", dogstatsd=statsd, sync_mode=False, report_metrics=False)

            # Queue 3 health metrics where each metric has the same name but different tags
            writer.write([Span(name="name", trace_id=1, span_id=1, parent_id=None)])
            writer._metrics_dist("test_trace.queued", 1, ["k1:v1"])
            writer.write([Span(name="name", trace_id=2, span_id=2, parent_id=None)])
            writer._metrics_dist(
                "test_trace.queued",
                1,
                [
                    "k2:v2",
                    "k22:v22",
                ],
            )
            writer.write([Span(name="name", trace_id=3, span_id=3, parent_id=None)])
            writer._metrics_dist("test_trace.queued", 1)

            # Ensure that the metrics are not reported
            call_args = statsd.distribution.call_args
            if call_args is not None:
                assert (
                    mock.call("datadog.%s.test_trace.queued" % writer.STATSD_NAMESPACE, 1, tags=["k1:v1"])
                    not in call_args
                )
                assert (
                    mock.call("datadog.%s.test_trace.queued" % writer.STATSD_NAMESPACE, 1, tags=["k2:v2", "k22:v22"])
                    not in call_args
                )
                assert (
                    mock.call("datadog.%s.test_trace.queued" % writer.STATSD_NAMESPACE, 1, tags=None) not in call_args
                )

    def test_write_sync(self):
        statsd = mock.Mock()
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = self.WRITER_CLASS("http://asdf:1234", dogstatsd=statsd, sync_mode=True)
            writer.write([Span(name="name", trace_id=1, span_id=j + 1, parent_id=j or None) for j in range(5)])
            statsd.distribution.assert_has_calls(
                [
                    mock.call("datadog.%s.buffer.accepted.traces" % writer.STATSD_NAMESPACE, 1, tags=None),
                    mock.call("datadog.%s.buffer.accepted.spans" % writer.STATSD_NAMESPACE, 5, tags=None),
                    mock.call("datadog.%s.http.errors" % writer.STATSD_NAMESPACE, 1, tags=["type:err"]),
                    mock.call("datadog.%s.http.dropped.bytes" % writer.STATSD_NAMESPACE, AnyInt(), tags=None),
                ]
                + [mock.call("datadog.%s.http.requests" % writer.STATSD_NAMESPACE, 1, tags=None)]
                * writer.RETRY_ATTEMPTS,
                any_order=True,
            )

    def test_drop_reason_bad_endpoint(self):
        statsd = mock.Mock()
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = self.WRITER_CLASS("http://asdf:1234", dogstatsd=statsd, sync_mode=False)
            for i in range(10):
                writer.write([Span(name="name", trace_id=i, span_id=j + 1, parent_id=j or None) for j in range(5)])
            writer.stop()
            writer.join()

            # metrics should be reset after traces are sent
            assert writer._metrics == {"accepted_traces": 0, "sent_traces": 0}
            statsd.distribution.assert_has_calls(
                [
                    mock.call("datadog.%s.http.errors" % writer.STATSD_NAMESPACE, 1, tags=["type:err"]),
                    mock.call("datadog.%s.http.dropped.traces" % writer.STATSD_NAMESPACE, 10, tags=None),
                ],
                any_order=True,
            )

    def test_drop_reason_trace_too_big(self):
        statsd = mock.Mock()
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = self.WRITER_CLASS("http://asdf:1234", dogstatsd=statsd, buffer_size=1000)
            for i in range(10):
                writer.write([Span(name="name", trace_id=i, span_id=j + 1, parent_id=j or None) for j in range(5)])
            writer.write([Span(name="a" * i, trace_id=i, span_id=j + 1, parent_id=j or None) for j in range(2**10)])
            writer.stop()
            writer.join()

            # metrics should be reset after traces are sent
            assert writer._metrics == {"accepted_traces": 0, "sent_traces": 0}

        statsd.distribution.assert_has_calls(
            [
                mock.call(
                    "datadog.%s.buffer.dropped.traces" % writer.STATSD_NAMESPACE,
                    1,
                    tags=["reason:t_too_big"],
                ),
            ],
            any_order=True,
        )

    def test_drop_reason_buffer_full(self):
        statsd = mock.Mock()
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = self.WRITER_CLASS("http://asdf:1234", buffer_size=1000, dogstatsd=statsd)
            for i in range(10):
                writer.write([Span(name="name", trace_id=i, span_id=j + 1, parent_id=j or None) for j in range(5)])
            writer.write([Span(name="a", trace_id=i, span_id=j + 1, parent_id=j or None) for j in range(5)])
            writer.stop()
            writer.join()

            # metrics should be reset after traces are sent
            assert writer._metrics == {"accepted_traces": 0, "sent_traces": 0}

            client_count = len(writer._clients)
            statsd.distribution.assert_has_calls(
                [
                    mock.call(
                        "datadog.%s.buffer.dropped.traces" % writer.STATSD_NAMESPACE, client_count, tags=["reason:full"]
                    ),
                ],
                any_order=True,
            )

    def test_drop_reason_encoding_error(self):
        n_traces = 10
        statsd = mock.Mock()
        writer_encoder = mock.Mock()
        writer_encoder.__len__ = (lambda *args: n_traces).__get__(writer_encoder)
        writer_encoder.encode.side_effect = Exception
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = self.WRITER_CLASS("http://asdf:1234", dogstatsd=statsd, sync_mode=False)
            for client in writer._clients:
                client.encoder = writer_encoder
            for i in range(n_traces):
                writer.write(
                    [Span(name="name", trace_id=i, span_id=j, parent_id=max(0, j - 1) or None) for j in range(5)]
                )

            writer.stop()
            writer.join()

            # writer should have has 10 unsent traces, sent traces should be reset to zero
            assert writer._metrics == {"accepted_traces": 10, "sent_traces": 0}
            statsd.distribution.assert_has_calls(
                [
                    mock.call("datadog.%s.encoder.dropped.traces" % writer.STATSD_NAMESPACE, n_traces, tags=None),
                ]
                * len(writer._clients),
                any_order=True,
            )

    def test_keep_rate(self):
        statsd = mock.Mock()
        writer_run_periodic = mock.Mock()
        writer_put = mock.Mock()
        writer_put.return_value = Response(status=200)
        with override_global_config(dict(_health_metrics_enabled=False, _trace_writer_buffer_size=8 << 20)):
            # this test decodes the msgpack payload to verify the keep rate. v04 is easier to decode so we use that here
            writer = self.WRITER_CLASS("http://asdf:1234", dogstatsd=statsd, api_version="v0.4")
            writer.run_periodic = writer_run_periodic
            writer._put = writer_put

            traces = [
                [Span(name="name", trace_id=i, span_id=j + 1, parent_id=j) for j in range(5)] for i in range(1, 5)
            ]

            traces_too_big = [
                [Span(name="a" * 5000, trace_id=i, span_id=j + 1, parent_id=j) for j in range(1, 2**10)]
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
                assert 1.0 == trace[0]["metrics"].get(_KEEP_SPANS_RATE_KEY, -1)

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
                assert 0.5 == trace[0]["metrics"].get(_KEEP_SPANS_RATE_KEY, -1)

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
                assert 0.6 == trace[0]["metrics"].get(_KEEP_SPANS_RATE_KEY, -1)


class CIVisibilityWriterTests(AgentWriterTests):
    WRITER_CLASS = CIVisibilityWriter

    def setUp(self):
        self.original_env = dict(os.environ)
        os.environ.update(dict(DD_API_KEY="foobar.baz"))

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)

    # NB these tests are skipped because they exercise max_payload_size and max_item_size functionality
    # that CIVisibilityWriter does not implement
    def test_drop_reason_buffer_full(self):
        pytest.skip()

    def test_drop_reason_trace_too_big(self):
        pytest.skip()

    def test_metrics_trace_too_big(self):
        pytest.skip()

    def test_keep_rate(self):
        pytest.skip()

    def test_metadata_included(self):
        writer = CIVisibilityWriter("http://localhost:9126")
        for client in writer._clients:
            client.encoder.put([Span("foobar")])
            payload = client.encoder.encode()[0]
            try:
                unpacked_metadata = msgpack.unpackb(payload, raw=True, strict_map_key=False)[b"metadata"][b"*"]
            except KeyError:
                continue
            assert unpacked_metadata[b"language"] == b"python"
            assert unpacked_metadata[b"runtime-id"] == get_runtime_id().encode("utf-8")
            assert unpacked_metadata[b"library_version"] == ddtrace.__version__.encode("utf-8")
            assert unpacked_metadata[b"env"] == (config.env.encode("utf-8") if config.env else None)
            return
        pytest.fail("At least one ci visibility payload must include metadata")


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


class _BaseHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
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


class UDSHTTPServer(socketserver.UnixStreamServer, http.server.HTTPServer):
    def server_bind(self):
        http.server.HTTPServer.server_bind(self)


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
            resp = conn.getresponse().status
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
    server = http.server.HTTPServer((_HOST, port), request_handler)
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


@pytest.mark.parametrize("writer_and_path", ((AgentWriter, "/v0."), (CIVisibilityWriter, "/api/v2/citestcycle")))
def test_agent_url_path(endpoint_assert_path, writer_and_path):
    with override_env(dict(DD_API_KEY="foobar.baz")):
        writer_class, path = writer_and_path
        # test without base path
        endpoint_assert_path(path)
        writer = writer_class("http://%s:%s/" % (_HOST, _PORT))
        writer._encoder.put([Span("foobar")])
        writer.flush_queue(raise_exc=True)

        # test without base path nor trailing slash
        writer = writer_class("http://%s:%s" % (_HOST, _PORT))
        writer._encoder.put([Span("foobar")])
        writer.flush_queue(raise_exc=True)

        # test with a base path
        endpoint_assert_path("/test%s" % path)
        writer = writer_class("http://%s:%s/test/" % (_HOST, _PORT))
        writer._encoder.put([Span("foobar")])
        writer.flush_queue(raise_exc=True)


@pytest.mark.parametrize("writer_class", (AgentWriter, CIVisibilityWriter))
def test_flush_connection_timeout_connect(writer_class):
    with override_env(dict(DD_API_KEY="foobar.baz")):
        writer = writer_class("http://%s:%s" % (_HOST, 2019))
        exc_type = OSError
        with pytest.raises(exc_type):
            writer._encoder.put([Span("foobar")])
            writer.flush_queue(raise_exc=True)


@pytest.mark.parametrize("writer_class", (AgentWriter, CIVisibilityWriter))
def test_flush_connection_timeout(endpoint_test_timeout_server, writer_class):
    with override_env(dict(DD_API_KEY="foobar.baz")):
        writer = writer_class("http://%s:%s" % (_HOST, _TIMEOUT_PORT))
        writer.HTTP_METHOD = "PUT"  # the test server only accepts PUT
        with pytest.raises(socket.timeout):
            writer._encoder.put([Span("foobar")])
            writer.flush_queue(raise_exc=True)


@pytest.mark.parametrize("writer_class", (AgentWriter, CIVisibilityWriter))
def test_flush_connection_reset(endpoint_test_reset_server, writer_class):
    with override_env(dict(DD_API_KEY="foobar.baz")):
        writer = writer_class("http://%s:%s" % (_HOST, _RESET_PORT))
        exc_types = (httplib.BadStatusLine, ConnectionResetError)
        with pytest.raises(exc_types):
            writer.HTTP_METHOD = "PUT"  # the test server only accepts PUT
            writer._encoder.put([Span("foobar")])
            writer.flush_queue(raise_exc=True)


@pytest.mark.parametrize("writer_class", (AgentWriter,))
def test_flush_connection_uds(endpoint_uds_server, writer_class):
    writer = writer_class("unix://%s" % endpoint_uds_server.server_address)
    writer._encoder.put([Span("foobar")])
    writer.flush_queue(raise_exc=True)


@pytest.mark.parametrize("writer_class", (AgentWriter, CIVisibilityWriter))
def test_flush_queue_raise(writer_class):
    with override_env(dict(DD_API_KEY="foobar.baz")):
        writer = writer_class("http://dne:1234")

        # Should not raise
        writer.write([])
        writer.flush_queue(raise_exc=False)

        error = OSError
        with pytest.raises(error):
            writer.write([Span("name")])
            writer.flush_queue(raise_exc=True)


@pytest.mark.parametrize("writer_class", (AgentWriter,))
def test_racing_start(writer_class):
    writer = writer_class("http://dne:1234")

    def do_write(i):
        writer.write([Span(str(i))])

    ts = [threading.Thread(target=do_write, args=(i,)) for i in range(100)]
    for t in ts:
        t.start()

    for t in ts:
        t.join()

    assert len(writer._encoder) == 100


@pytest.mark.subprocess(
    env={"_DD_TRACE_WRITER_ADDITIONAL_HEADERS": "additional-header:additional-value,header2:value2"}
)
def test_additional_headers():
    from ddtrace.internal.writer import AgentWriter

    writer = AgentWriter("http://localhost:9126")
    assert writer._headers["additional-header"] == "additional-value"
    assert writer._headers["header2"] == "value2"


def test_additional_headers_constructor():
    writer = AgentWriter(
        agent_url="http://localhost:9126", headers={"additional-header": "additional-value", "header2": "value2"}
    )
    assert writer._headers["additional-header"] == "additional-value"
    assert writer._headers["header2"] == "value2"


@pytest.mark.parametrize("writer_class", (AgentWriter,))
def test_bad_encoding(monkeypatch, writer_class):
    with override_global_config({"_trace_api": "foo"}):
        writer = writer_class("http://localhost:9126")
        assert writer._api_version == "v0.5"


@pytest.mark.parametrize(
    "init_api_version,api_version,endpoint,encoder_cls",
    [
        (None, "v0.5", "v0.5/traces", MSGPACK_ENCODERS["v0.5"]),
        ("v0.4", "v0.4", "v0.4/traces", MSGPACK_ENCODERS["v0.4"]),
        ("v0.5", "v0.5", "v0.5/traces", MSGPACK_ENCODERS["v0.5"]),
    ],
)
def test_writer_recreate_api_version(init_api_version, api_version, endpoint, encoder_cls):
    writer = AgentWriter("http://dne:1234", api_version=init_api_version)
    assert writer._api_version == api_version
    assert writer._endpoint == endpoint
    assert isinstance(writer._encoder, encoder_cls)

    writer = writer.recreate()
    assert writer._api_version == api_version
    assert writer._endpoint == endpoint
    assert isinstance(writer._encoder, encoder_cls)


def test_writer_recreate_keeps_headers():
    writer = AgentWriter("http://dne:1234", headers={"Datadog-Client-Computed-Stats": "yes"})
    assert "Datadog-Client-Computed-Stats" in writer._headers
    assert writer._headers["Datadog-Client-Computed-Stats"] == "yes"

    writer = writer.recreate()
    assert "Datadog-Client-Computed-Stats" in writer._headers
    assert writer._headers["Datadog-Client-Computed-Stats"] == "yes"


@pytest.mark.parametrize(
    "sys_platform, api_version, ddtrace_api_version, raises_error, expected",
    [
        # -- win32
        # Defaults on windows
        ("win32", None, None, False, "v0.4"),
        # Env variable is used if explicit value is not given
        ("win32", None, "v0.4", False, "v0.4"),
        ("win32", None, "v0.4", False, "v0.4"),
        # v0.5 is not supported on windows
        ("win32", "v0.5", None, True, None),
        ("win32", "v0.5", None, True, None),
        ("win32", "v0.5", "v0.4", True, None),
        ("win32", None, "v0.5", True, None),
        # -- cygwin
        # Defaults on windows
        ("cygwin", None, None, False, "v0.4"),
        # Default with priority sampler
        ("cygwin", None, None, False, "v0.4"),
        # Env variable is used if explicit value is not given
        ("cygwin", None, "v0.4", False, "v0.4"),
        ("cygwin", None, "v0.4", False, "v0.4"),
        # v0.5 is not supported on windows
        ("cygwin", "v0.5", None, True, None),
        ("cygwin", "v0.5", None, True, None),
        ("cygwin", "v0.5", "v0.4", True, None),
        ("cygwin", None, "v0.5", True, None),
        # -- Non-windows
        # defaults
        ("darwin", None, None, False, "v0.5"),
        # Default with priority sample
        ("darwin", None, None, False, "v0.5"),
        # Explicitly setting api version
        ("darwin", "v0.4", None, False, "v0.4"),
        # Explicitly set version takes precedence
        ("darwin", "v0.4", "v0.5", False, "v0.4"),
        # Via env variable
        ("darwin", None, "v0.4", False, "v0.4"),
        ("darwin", None, "v0.5", False, "v0.5"),
    ],
)
@pytest.mark.parametrize("writer_class", (AgentWriter,))
def test_writer_api_version_selection(
    sys_platform,
    api_version,
    ddtrace_api_version,
    raises_error,
    expected,
    monkeypatch,
    writer_class,
):
    """test to verify that we are unable to select v0.5 api version when on a windows machine.

    https://docs.python.org/3/library/sys.html#sys.platform

    The possible ``sys.platform`` values when on windows are ``win32`` or ``cygwin``.
    """

    # Mock the value of `sys.platform` to be a specific value
    with mock_sys_platform(sys_platform):
        try:
            # Create a new writer
            if ddtrace_api_version is not None:
                with override_global_config({"_trace_api": ddtrace_api_version}):
                    writer = writer_class("http://dne:1234", api_version=api_version)
            else:
                writer = writer_class("http://dne:1234", api_version=api_version)
            assert writer._api_version == expected
        except RuntimeError:
            # If we were not expecting a RuntimeError, then cause the test to fail
            if not raises_error:
                pytest.fail("Raised RuntimeError when it was not expected")


@pytest.mark.parametrize("writer_class", (AgentWriter, CIVisibilityWriter))
def test_writer_reuse_connections_envvar(monkeypatch, writer_class):
    with override_env(dict(DD_API_KEY="foobar.baz")):
        with override_global_config({"_trace_writer_connection_reuse": False}):
            writer = writer_class("http://localhost:9126")
            assert not writer._reuse_connections

        with override_global_config({"_trace_writer_connection_reuse": True}):
            writer = writer_class("http://localhost:9126")
            assert writer._reuse_connections


@pytest.mark.parametrize("writer_class", (AgentWriter, CIVisibilityWriter))
def test_writer_reuse_connections(writer_class):
    with override_env(dict(DD_API_KEY="foobar.baz")):
        # Ensure connection is not reused
        writer = writer_class("http://localhost:9126", reuse_connections=True)
        # Do an initial flush to get a connection
        writer.flush_queue()
        assert writer._conn is None
        writer.flush_queue()
        assert writer._conn is None


@pytest.mark.parametrize("writer_class", (AgentWriter, CIVisibilityWriter))
def test_writer_reuse_connections_false(writer_class):
    with override_env(dict(DD_API_KEY="foobar.baz")):
        # Ensure connection is reused
        writer = writer_class("http://localhost:9126", reuse_connections=False)
        # Do an initial flush to get a connection
        writer.flush_queue()
        conn = writer._conn
        # And another to potentially have it reset
        writer.flush_queue()
        assert writer._conn is conn


@pytest.mark.subprocess(env=dict(DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="true"))
def test_trace_with_128bit_trace_ids():
    """Ensure 128bit trace ids are correctly encoded"""
    from ddtrace.internal.constants import HIGHER_ORDER_TRACE_ID_BITS
    from tests.utils import DummyTracer

    tracer = DummyTracer()

    with tracer.trace("parent") as parent:
        with tracer.trace("child1"):
            pass
        with tracer.trace("child2"):
            pass

    spans = tracer.pop()
    chunk_root = spans[0]
    assert chunk_root.trace_id >= 2**64
    assert chunk_root._meta[HIGHER_ORDER_TRACE_ID_BITS] == "{:016x}".format(parent.trace_id >> 64)
