"""Tests for NativeTraceBuffer, the opt-in writer backed by libdatadog's own trace buffer.

The buffer exports from a libdatadog worker thread, so every assertion about delivery here polls
rather than assuming the send already happened.
"""

import contextlib
import socket
import threading
import time
from unittest import mock

import msgpack
import pytest

from ddtrace.internal.native_runtime import get_native_runtime
from ddtrace.internal.service import ServiceStatusError
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings._opentelemetry import otel_config
from ddtrace.internal.writer import NativeTraceBuffer
from ddtrace.internal.writer import NativeWriter
from ddtrace.internal.writer import writer as writer_module
from ddtrace.internal.writer.writer import create_trace_writer
from ddtrace.trace import Span
from tests.tracer.test_writer import _HOST
from tests.tracer.test_writer import _BaseHTTPRequestHandler
from tests.tracer.test_writer import _make_server


_BUFFER_PORT = 8749


class _CaptureHandler(_BaseHTTPRequestHandler):
    """Collect every request body the tracer sends, so a test can decode the wire payload.

    The bodies go to the payloads list of the server, so that each test sees only its own exports.
    """

    def _capture(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.server.payloads.append(self.rfile.read(length))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_PUT(self):
        self._capture()

    def do_POST(self):
        self._capture()


def _start_capture_server(handler=_CaptureHandler):
    """Start a capture server on a free port.

    A fixed port receives the late exports of a buffer from an earlier test, because a shutdown that
    times out leaves the export of that buffer running.
    """
    server, thread = _make_server(0, handler)
    server.payloads = []
    server.port = server.server_address[1]
    return server, thread


@contextlib.contextmanager
def capture_server(handler=_CaptureHandler):
    server, thread = _start_capture_server(handler)
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()


@pytest.fixture
def agent():
    """Answer every export, so that the shutdown of a buffer ends within its budget."""
    with capture_server() as server:
        yield server


@contextlib.contextmanager
def black_hole_server():
    """Accept connections but never answer, so a wait against it ends only on its own timeout."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    sock.settimeout(0.1)
    stop = threading.Event()
    conns: list = []

    def accept_loop():
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
                conns.append(conn)
            except socket.timeout:
                continue

    thread = threading.Thread(target=accept_loop)
    thread.start()
    try:
        yield sock.getsockname()[1]
    finally:
        stop.set()
        thread.join()
        for conn in conns:
            conn.close()
        sock.close()


@contextlib.contextmanager
def buffer_writer(port, **kwargs):
    """Build a NativeTraceBuffer and shut it down again.

    NativeTraceBuffer has no start(): libdatadog spawns the worker when the buffer is constructed,
    which is why managed_writer from test_writer.py does not fit here.
    """
    writer = NativeTraceBuffer("http://%s:%s" % (_HOST, port), **kwargs)
    try:
        yield writer
    finally:
        writer.stop(3.0)


def _finished_span(name="op", trace_id=1, span_id=1):
    span = Span(name=name, trace_id=trace_id, span_id=span_id)
    span.finish()
    return span


def _wait_for_payload(handler, timeout=10.0):
    """Poll until the worker delivers something, because the export is asynchronous."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if handler.payloads:
            return b"".join(handler.payloads)
        time.sleep(0.02)
    return b""


def test_flag_off_selects_the_python_writer():
    # The default path must stay on NativeWriter, so shipping this dark changes nothing.
    writer = create_trace_writer()
    try:
        assert isinstance(writer, NativeWriter)
    finally:
        # NativeWriter starts on its first write, and stopping a service that never started raises.
        with contextlib.suppress(ServiceStatusError):
            writer.stop(3.0)


@pytest.mark.subprocess(env={"_DD_TRACE_NATIVE_BUFFER_ENABLED": "true"})
def test_flag_on_selects_the_native_trace_buffer():
    # Read the variable the way a user sets it. override_global_config filters against a whitelist and
    # would drop this key without failing, which hides whether the variable reaches config at all.
    from ddtrace.internal.writer import NativeTraceBuffer
    from ddtrace.trace import tracer

    assert isinstance(tracer._span_aggregator.writer, NativeTraceBuffer)


def test_write_then_flush_reaches_the_agent():
    # The end-to-end smoke test: a span written through the buffer arrives as a decodable v0.4
    # payload carrying the span's name.
    with capture_server() as handler:
        with buffer_writer(handler.port, api_version="v0.4") as writer:
            writer.write([_finished_span(name="smoke")])
            writer.flush_queue()
            body = _wait_for_payload(handler)

    assert body, "the buffer never delivered a payload"
    decoded = msgpack.unpackb(body, raw=False, strict_map_key=False)
    names = [span["name"] for chunk in decoded for span in chunk]
    assert "smoke" in names


def test_write_stamps_the_client_keep_rate(agent):
    # The backend scales its dropped-trace estimate by _dd.tracer_kr, and the snapshot suite asserts
    # it on every root span.
    span = _finished_span()
    with buffer_writer(agent.port) as writer:
        writer.write([span])
    assert span._get_numeric_attribute("_dd.tracer_kr") == 1.0


@pytest.mark.parametrize(
    "sabotage",
    [
        # Context.dd_origin does not validate what it stores, so write() can be handed any object.
        pytest.param(lambda span: setattr(span.context, "dd_origin", 5), id="non_string_origin"),
        # packb rejects a value it cannot serialize.
        pytest.param(lambda span: span._set_struct_tag("k", {"v": object()}), id="unpackable_meta_struct"),
    ],
)
def test_write_never_raises_into_user_code(sabotage, agent):
    # write() runs from Span.finish() inside a `with tracer.trace(...)` block, so an exception here
    # escapes into application code.
    span = Span(name="op", trace_id=1, span_id=1)
    sabotage(span)
    span.finish()
    with buffer_writer(agent.port) as writer:
        writer.write([span])


def test_write_reports_unreadable_spans_without_raising(agent):
    # A non-SpanData item cannot be projected. write() must report it rather than raise, and the
    # writer logs the reason.
    with buffer_writer(agent.port) as writer:
        reason = writer._buffer.write(["not a span"], None)
    assert reason is not None
    assert "not readable" in reason


def test_flush_queue_blocks_until_the_agent_receives():
    # flush_queue() now waits for the export instead of only triggering it, so the payload must
    # already be there the instant it returns, with no poll required.
    started = threading.Event()

    class _SlowHandler(_CaptureHandler):
        def _capture(self):
            started.set()
            time.sleep(0.2)
            super()._capture()

    with capture_server(_SlowHandler) as server:
        with buffer_writer(server.port, api_version="v0.4") as writer:
            writer.write([_finished_span(name="blocking")])
            writer.flush_queue()
            assert server.payloads, "flush_queue returned before the export was delivered"


def test_flush_queue_leaves_the_buffer_usable():
    # flush() must not shut anything down: a later write and flush have to reach the agent too. Each
    # flush delivers its own msgpack document, so decode with an Unpacker instead of assuming the
    # accumulated bytes form a single document.
    with capture_server() as handler:
        with buffer_writer(handler.port, api_version="v0.4") as writer:
            writer.write([_finished_span(name="first")])
            writer.flush_queue()
            assert _wait_for_payload(handler), "the first flush never delivered a payload"
            before = len(handler.payloads)
            writer.write([_finished_span(name="second")])
            writer.flush_queue()
            deadline = time.monotonic() + 10.0
            while len(handler.payloads) <= before and time.monotonic() < deadline:
                time.sleep(0.02)
            body = b"".join(handler.payloads)

    assert body, "no payload was delivered"
    unpacker = msgpack.Unpacker(raw=False, strict_map_key=False)
    unpacker.feed(body)
    names = [span["name"] for decoded in unpacker for chunk in decoded for span in chunk]
    assert "second" in names, names


def test_flush_queue_on_an_idle_buffer_returns_promptly(agent):
    # An idle buffer has nothing to wait on, so the wait libdatadog performs must return immediately
    # rather than block for the full agent timeout.
    with buffer_writer(agent.port) as writer:
        start = time.monotonic()
        writer.flush_queue()
        elapsed = time.monotonic() - start
    assert elapsed < 1.0, elapsed


def test_flush_queue_timeout_is_not_reported_as_lost_spans(monkeypatch):
    # Same contract as write()'s own TimedOut handling, but through the blocking flush_queue() path.
    # _flush_timeout_ns() floors the wait at 1 second, so the handler has to run well past that for
    # the flush to time out before the export finishes.
    monkeypatch.setattr(agent_config, "trace_agent_timeout_seconds", 0.1)

    class _SlowHandler(_CaptureHandler):
        def _capture(self):
            time.sleep(2.0)
            super()._capture()

    with capture_server(_SlowHandler) as server:
        with buffer_writer(server.port, api_version="v0.4") as writer:
            writer.write([_finished_span()])
            with mock.patch.object(writer_module, "_safelog") as safelog:
                writer.flush_queue()

    logged = [call.args for call in safelog.call_args_list]
    assert len(logged) == 1, logged
    _, message, reason = logged[0]
    assert "TimedOut" in reason, reason
    assert "dropped" not in message, message
    assert "failed" not in message, message


def test_flush_queue_with_a_non_positive_timeout_does_not_raise(monkeypatch, agent):
    # A configured timeout of 0 must not become a native zero-timeout wait: libdatadog reports that as
    # TimedOut for a flush that a moment later still succeeds, which would raise on every flush.
    monkeypatch.setattr(agent_config, "trace_agent_timeout_seconds", 0)
    with buffer_writer(agent.port) as writer:
        writer.write([_finished_span()])
        writer.flush_queue(raise_exc=True)


def test_flush_queue_inside_the_fork_window_does_not_block(agent):
    # A blocking flush issued while _paused is true would wait on a condvar nothing can notify, since
    # no runtime exists in that window to service it. flush_queue() must fall back to a fire-and-forget
    # flush instead.
    runtime = get_native_runtime()
    with buffer_writer(agent.port) as writer:
        writer.write([_finished_span()])
        runtime.before_fork()
        try:
            start = time.monotonic()
            writer.flush_queue()
            elapsed = time.monotonic() - start
        finally:
            runtime.after_fork_parent()
    assert elapsed < 1.0, elapsed


def _registered_workers(runtime):
    """Count the workers registered on the shared runtime.

    ForkSafeRuntime's Debug form prints one WorkerEntry per registered worker, and a worker stays
    registered until something calls stop() on its handle. Nothing else exposes the count.
    """
    return runtime.debug().count("WorkerEntry {")


@pytest.mark.subprocess()
def test_shutdown_reclaims_the_exporters_own_workers():
    # The buffer owns one worker, and the exporter it consumed owns more: agent-info, telemetry and
    # dogstatsd, plus the OTLP stats exporter. Only TraceExporter::shutdown reclaims those, and it
    # consumes the exporter, so a buffer that merely holds it inside its Export impl leaks them on
    # every stop() and every recreate(). The worker count is process-wide, and a buffer from another
    # test can shut down in the background, so the test runs in its own process.
    from ddtrace.internal.native_runtime import get_native_runtime
    from ddtrace.internal.writer import NativeTraceBuffer
    from tests.tracer.test_native_trace_buffer import _BUFFER_PORT
    from tests.tracer.test_native_trace_buffer import _registered_workers
    from tests.tracer.test_writer import _HOST

    runtime = get_native_runtime()
    before = _registered_workers(runtime)
    writer = NativeTraceBuffer("http://%s:%s" % (_HOST, _BUFFER_PORT), api_version="v0.4")
    try:
        assert _registered_workers(runtime) > before, "the buffer registered no worker"
    finally:
        writer.stop(3.0)
    assert _registered_workers(runtime) == before


@pytest.mark.subprocess()
def test_shutdown_reclaims_the_exporter_around_an_export_in_flight():
    # The shutdown must reclaim the exporter only after the buffer's worker has stopped, because that
    # worker may still be inside a send and the send holds the exporter for its duration. Reaching for
    # the exporter first finds it busy and gives up on it, which leaks its workers exactly when a
    # shutdown races a slow agent. The handler here answers slowly, so the export is in flight when
    # stop() runs. The worker count is process-wide, so the test runs in its own process.
    import threading
    import time

    import msgpack

    from ddtrace.internal.native_runtime import get_native_runtime
    from ddtrace.internal.writer import NativeTraceBuffer
    from tests.tracer.test_native_trace_buffer import _CaptureHandler
    from tests.tracer.test_native_trace_buffer import _finished_span
    from tests.tracer.test_native_trace_buffer import _registered_workers
    from tests.tracer.test_native_trace_buffer import _start_capture_server
    from tests.tracer.test_native_trace_buffer import _wait_for_payload
    from tests.tracer.test_writer import _HOST

    started = threading.Event()

    class _SlowHandler(_CaptureHandler):
        def _capture(self):
            started.set()
            time.sleep(0.5)
            super()._capture()

    runtime = get_native_runtime()
    server, server_thread = _start_capture_server(_SlowHandler)
    try:
        before = _registered_workers(runtime)
        writer = NativeTraceBuffer("http://%s:%s" % (_HOST, server.port), api_version="v0.4")
        writer.write([_finished_span(name="in-flight")])
        writer.flush_queue()
        assert started.wait(10.0), "the agent never received the export"
        writer.stop(5.0)
        assert _registered_workers(runtime) == before
        body = _wait_for_payload(server)
    finally:
        server.shutdown()
        server_thread.join()

    assert body, "the export in flight at shutdown was lost"
    decoded = msgpack.unpackb(body, raw=False, strict_map_key=False)
    names = [span["name"] for chunk in decoded for span in chunk]
    assert "in-flight" in names


def _held_handler(started, release):
    """Return a request handler that holds each request until release is set.

    The test server handles one request at a time, so a held request also holds every later
    request to the same server.
    """

    class _HeldHandler(_CaptureHandler):
        def _capture(self):
            started.set()
            release.wait(10.0)
            super()._capture()

    return _HeldHandler


def _stop_with_an_export_held(port, started):
    """Return a NativeTraceBuffer whose stop() timed out while its export was in flight."""
    writer = NativeTraceBuffer("http://%s:%s" % (_HOST, port), api_version="v0.4")
    writer.write([_finished_span(name="held")])
    # flush_queue() would wait for the held export and time out.
    writer._buffer.force_flush()
    assert started.wait(10.0), "the agent never received the export"
    writer.stop(0.001)
    return writer


def _wait_for_the_export_to_end(writer, timeout=10.0):
    """Poll until the response handler runs, which happens only after the export returns."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if writer._buffer.take_agent_response() is not None:
            return True
        time.sleep(0.01)
    return False


@pytest.mark.subprocess()
def test_a_timed_out_shutdown_leaves_the_exporter_workers_to_the_runtime():
    # When the worker cannot stop within the budget, the export in flight keeps the exporter. The
    # exporter workers must stay registered after that export ends, because only a registered worker
    # is paused before a fork and stopped by the runtime shutdown at exit. The worker count is
    # process-wide, so the test runs in its own process.
    import threading

    from ddtrace.internal.native_runtime import get_native_runtime
    from tests.tracer.test_native_trace_buffer import _held_handler
    from tests.tracer.test_native_trace_buffer import _registered_workers
    from tests.tracer.test_native_trace_buffer import _start_capture_server
    from tests.tracer.test_native_trace_buffer import _stop_with_an_export_held
    from tests.tracer.test_native_trace_buffer import _wait_for_the_export_to_end

    started = threading.Event()
    release = threading.Event()
    runtime = get_native_runtime()
    server, server_thread = _start_capture_server(_held_handler(started, release))
    try:
        before = _registered_workers(runtime)
        writer = _stop_with_an_export_held(server.port, started)
        held = _registered_workers(runtime)
        assert held > before, "the exporter workers were not registered"
        release.set()
        assert _wait_for_the_export_to_end(writer), "the held export never ended"
        assert _registered_workers(runtime) == held
        runtime.shutdown(timeout_ms=3000)
        assert _registered_workers(runtime) == 0
    finally:
        release.set()
        server.shutdown()
        server_thread.join()


@pytest.mark.subprocess()
def test_a_timed_out_shutdown_reclaims_the_exporter_when_the_export_ends_in_time():
    # The export in flight holds the exporter after the worker stop times out. When that export ends
    # inside the slice reserved for the exporter, the shutdown must still reclaim the exporter
    # workers, rather than leave them to the runtime for the rest of the process. The worker count is
    # process-wide, so the test runs in its own process.
    import threading
    import time

    from ddtrace.internal.native_runtime import get_native_runtime
    from tests.tracer.test_native_trace_buffer import _CaptureHandler
    from tests.tracer.test_native_trace_buffer import _registered_workers
    from tests.tracer.test_native_trace_buffer import _start_capture_server
    from tests.tracer.test_native_trace_buffer import _stop_with_an_export_held

    started = threading.Event()

    class _SlowHandler(_CaptureHandler):
        def _capture(self):
            started.set()
            time.sleep(0.2)
            super()._capture()

    runtime = get_native_runtime()
    server, server_thread = _start_capture_server(_SlowHandler)
    try:
        before = _registered_workers(runtime)
        _stop_with_an_export_held(server.port, started)
        after = _registered_workers(runtime)
        assert after == before, (before, after, runtime.debug())
    finally:
        server.shutdown()
        server_thread.join()


# Python 3.12 warns on stderr when a multi-threaded process forks.
@pytest.mark.subprocess(timeout=60, err=None, env={"DD_TRACE_WRITER_INTERVAL_SECONDS": "60"})
def test_a_forked_child_leaves_the_inherited_buffer_alone_on_stop():
    # A forked child that exits before it rebuilds its writer reaches stop() with the buffer of the
    # parent. A shutdown there would send the spans of the parent from the child, and would wait on a
    # worker that the fork dropped.
    import os
    import time

    from ddtrace.internal.writer import NativeTraceBuffer
    from tests.tracer.test_native_trace_buffer import _finished_span
    from tests.tracer.test_native_trace_buffer import _start_capture_server
    from tests.tracer.test_native_trace_buffer import _wait_for_payload
    from tests.tracer.test_writer import _HOST

    server, server_thread = _start_capture_server()
    try:
        writer = NativeTraceBuffer("http://%s:%s" % (_HOST, server.port), api_version="v0.4")
        writer.write([_finished_span(name="parent-only")])
        pid = os.fork()
        if pid == 0:
            start = time.monotonic()
            writer.stop(3.0)
            os._exit(0 if time.monotonic() - start < 1.0 else 1)
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, "the child stop() blocked"
        time.sleep(0.5)
        assert server.payloads == [], "the child sent the spans of the parent"
        writer.stop(3.0)
        assert _wait_for_payload(server), "the parent never sent its own spans"
    finally:
        server.shutdown()
        server_thread.join()


# Python 3.12 warns on stderr when a multi-threaded process forks.
@pytest.mark.subprocess(timeout=60, err=None)
def test_fork_after_a_timed_out_shutdown_does_not_hang():
    # The export held by a timed-out shutdown can end while os.fork() pauses the runtime workers. The
    # runtime has one thread, and the pause holds the worker registry, so an export that touched the
    # registry from that thread would block the pause forever. A second buffer keeps its own export
    # in flight, so the pause is still waiting when the first export ends.
    import os
    import sys
    import threading
    import time

    from ddtrace.internal.writer import NativeTraceBuffer
    from tests.tracer.test_native_trace_buffer import _finished_span
    from tests.tracer.test_native_trace_buffer import _held_handler
    from tests.tracer.test_native_trace_buffer import _start_capture_server
    from tests.tracer.test_native_trace_buffer import _stop_with_an_export_held
    from tests.tracer.test_native_trace_buffer import _wait_for_the_export_to_end
    from tests.tracer.test_writer import _HOST

    held_started, held_release = threading.Event(), threading.Event()
    busy_started, busy_release = threading.Event(), threading.Event()
    held_server, held_thread = _start_capture_server(_held_handler(held_started, held_release))
    busy_server, busy_thread = _start_capture_server(_held_handler(busy_started, busy_release))
    children = []

    def fork():
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        children.append(pid)

    try:
        held = _stop_with_an_export_held(held_server.port, held_started)
        busy = NativeTraceBuffer("http://%s:%s" % (_HOST, busy_server.port), api_version="v0.4")
        busy.write([_finished_span(name="busy")])
        busy._buffer.force_flush()
        assert busy_started.wait(10.0), "the agent never received the busy export"

        forker = threading.Thread(target=fork, daemon=True)
        forker.start()
        # Let the fork start to pause the workers. The pause then waits for the busy export.
        time.sleep(0.5)
        held_release.set()
        # Give the held export time to end while the pause still waits.
        time.sleep(0.5)
        busy_release.set()
        forker.join(10.0)
        if forker.is_alive():
            # The interpreter exit would block on the same runtime. Exit at once, so the hang fails
            # the test instead of the whole run.
            sys.stderr.write("os.fork() did not return\n")
            sys.stderr.flush()
            os._exit(1)

        assert children, "os.fork() returned no child"
        _, status = os.waitpid(children[0], 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, status
        assert _wait_for_the_export_to_end(held), "the held export never ended"
        busy.stop(3.0)
    finally:
        held_release.set()
        busy_release.set()
        for server, thread in ((held_server, held_thread), (busy_server, busy_thread)):
            server.shutdown()
            thread.join()


def test_shutdown_is_idempotent(agent):
    writer = NativeTraceBuffer("http://%s:%s" % (_HOST, agent.port))
    writer.stop(1.0)
    # A second stop must not raise: SpanAggregator.shutdown and atexit can both reach it.
    writer.stop(1.0)


def test_recreate_returns_a_fresh_writer(agent):
    with buffer_writer(agent.port) as writer:
        replacement = writer.recreate()
        try:
            assert replacement is not writer
            assert isinstance(replacement, NativeTraceBuffer)
            assert replacement.intake_url == writer.intake_url
        finally:
            replacement.stop(3.0)


def test_recreate_in_a_forked_child_does_not_resend_the_parent_spans():
    # In a forked child the inherited buffer holds spans that belong to the parent, which may already
    # have sent them, and libdatadog has dropped the worker that would send them. recreate() tells the
    # child apart from a reconfiguration by pid, so this emulates the child by pid alone rather than
    # forking the test runner.
    with capture_server() as handler:
        # Park the flush cadence, so only a flush this test asks for can deliver the span.
        with mock.patch.object(config, "_trace_writer_interval_seconds", 3600):
            with buffer_writer(handler.port, api_version="v0.4") as writer:
                writer.write([_finished_span(name="parent-span")])
                writer._pid = -1
                inherited_buffer = writer._buffer
                replacement = writer.recreate()
                try:
                    # Give a wrongly-flushing implementation time to deliver.
                    time.sleep(0.5)
                    assert not handler.payloads, "recreate() re-sent the parent's spans"
                    # The shutdown that recreate() skips here would refuse this span.
                    assert inherited_buffer.write([_finished_span()], None) is None
                finally:
                    replacement.stop(3.0)


def test_recreate_shuts_the_replaced_buffer_down(agent):
    # libdatadog reclaims a buffer's worker on shutdown only, and tracer.configure() reaches recreate()
    # on every ASM remote-config update, so a buffer that is merely dropped leaks a worker per call. A
    # shut-down buffer refuses further spans, which is what makes the shutdown observable from here.
    with buffer_writer(agent.port, api_version="v0.4") as writer:
        replaced_buffer = writer._buffer
        replacement = writer.recreate()
        try:
            assert replaced_buffer.write([_finished_span()], None) == "AlreadyClosed"
        finally:
            replacement.stop(3.0)


def test_agent_response_reaches_the_callback():
    # The response handler runs on a worker thread that must not touch Python, so it parks the body.
    # The writer collects it on the next write or flush and feeds the sampler.
    received = []

    class _RateHandler(_CaptureHandler):
        def _capture(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"rate_by_service": {"service:test,env:": 0.5}}')

    with capture_server(_RateHandler) as server:
        with buffer_writer(server.port, api_version="v0.4", response_callback=received.append) as writer:
            writer.write([_finished_span()])
            writer.flush_queue()
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not received:
                time.sleep(0.05)
                writer.flush_queue()

    assert received, "the agent response never reached the callback"
    assert received[0].rate_by_service == {"service:test,env:": 0.5}


def test_concurrent_writes_do_not_corrupt_the_buffer():
    # send_chunk takes the buffer's state mutex, so concurrent application threads are safe. This
    # pins that, because the buffer is reached from every request thread.
    with capture_server() as handler:
        with buffer_writer(handler.port, api_version="v0.4") as writer:

            def writer_thread(base):
                for i in range(20):
                    writer.write([_finished_span(trace_id=base, span_id=i + 1)])

            threads = [threading.Thread(target=writer_thread, args=(n + 1,)) for n in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            writer.flush_queue()
            body = _wait_for_payload(handler)

    assert body
    decoded = msgpack.unpackb(body, raw=False, strict_map_key=False)
    assert sum(len(chunk) for chunk in decoded) > 0


def test_write_then_flush_reaches_the_agent_v05():
    # v0.5 is the default api version, so this is the path most users of the flag would take. It also
    # runs libdatadog's v0.4 to v0.5 conversion, which builds a shared string table and copies each
    # span string into it.
    with capture_server() as handler:
        with buffer_writer(handler.port, api_version="v0.5") as writer:
            writer.write([_finished_span(name="smoke-v05")])
            writer.flush_queue()
            body = _wait_for_payload(handler)

    assert body, "the buffer never delivered a v0.5 payload"
    # A v0.5 payload is [string_table, traces], where every span field is an index into the table.
    string_table, traces = msgpack.unpackb(body, raw=False, strict_map_key=False)
    assert "smoke-v05" in string_table
    assert sum(len(chunk) for chunk in traces) > 0


@pytest.mark.parametrize("flags", [0, 1])
def test_v05_strips_the_span_link_flags_sentinel(flags):
    # Bit 31 of a link's flags marks "flags present" for the v0.4 wire form, and must not reach the
    # v0.5 JSON. An explicit flags 0 stays in the JSON, as the Cython v0.5 encoder writes it.
    span = Span(name="linked", trace_id=1, span_id=1)
    span._set_link(
        trace_id=0x1234567890ABCDEF1234567890ABCDEF,
        span_id=0x1122334455667788,
        flags=flags,
    )
    span.finish()

    with capture_server() as handler:
        with buffer_writer(handler.port, api_version="v0.5") as writer:
            writer.write([span])
            writer.flush_queue()
            body = _wait_for_payload(handler)

    assert body
    string_table, _ = msgpack.unpackb(body, raw=False, strict_map_key=False)
    links_json = [s for s in string_table if "span_id" in s and "flags" in s]
    assert links_json, "v0.5 output carried no JSON-encoded span links"
    assert '"flags":%d' % flags in links_json[0].replace(" ", ""), links_json[0]
    assert str(flags | 1 << 31) not in links_json[0], "the flags sentinel leaked into the v0.5 JSON"


def test_v05_carries_span_events():
    # v0.5 has no span_events wire field, so libdatadog JSON-encodes them into meta["events"].
    span = Span(name="evented", trace_id=1, span_id=1)
    span._add_event("my_event", attributes={"k": "v"}, time_unix_nano=1700000000000000000)
    span.finish()

    with capture_server() as handler:
        with buffer_writer(handler.port, api_version="v0.5") as writer:
            writer.write([span])
            writer.flush_queue()
            body = _wait_for_payload(handler)

    assert body
    string_table, _ = msgpack.unpackb(body, raw=False, strict_map_key=False)
    assert any("my_event" in s for s in string_table), "the span event never reached the payload"


def _encode_v04(span):
    with capture_server() as handler:
        with buffer_writer(handler.port, api_version="v0.4") as writer:
            writer.write([span])
            writer.flush_queue()
            body = _wait_for_payload(handler)

    assert body, "the buffer never delivered a payload"
    [[wire_span]] = msgpack.unpackb(body, raw=False, strict_map_key=False)
    return wire_span


@pytest.mark.parametrize("field", ["start_ns", "duration_ns"])
def test_negative_start_and_duration_encode_as_zero(field):
    # The Cython encoder clamps both fields to 0, so the native buffer must send the same values.
    span = _finished_span()
    setattr(span, field, -5)

    wire_span = _encode_v04(span)
    assert wire_span[field.replace("_ns", "")] == 0


@pytest.mark.parametrize("value", [5, 5.5, 2**60], ids=["int", "float", "big_int"])
def test_a_user_origin_tag_of_any_type_yields_to_the_trace_origin(value):
    span = _finished_span()
    span.context.dd_origin = "synthetics"
    span.set_metric("_dd.origin", value)  # ast-grep-ignore: span-set-metric

    wire_span = _encode_v04(span)
    assert wire_span["meta"]["_dd.origin"] == "synthetics"
    assert "_dd.origin" not in wire_span.get("metrics", {})


@pytest.mark.parametrize(
    "wrap",
    [
        pytest.param(tuple, id="tuple"),
        pytest.param(lambda spans: (span for span in spans), id="generator"),
    ],
)
def test_write_accepts_any_iterable_of_spans(wrap):
    # SpanAggregator.on_span_finish forwards whatever a user's TraceProcessor.process_trace returned,
    # so a tuple or a generator reaches write(). A typed parameter would raise from the argument layer,
    # out of Span.finish() and into application code.
    with capture_server() as handler:
        with buffer_writer(handler.port, api_version="v0.4") as writer:
            reason = writer._buffer.write(wrap([_finished_span(name="from-processor")]), None)
            writer.flush_queue()
            body = _wait_for_payload(handler)

    assert reason is None
    assert body, "the buffer never delivered a payload"
    decoded = msgpack.unpackb(body, raw=False, strict_map_key=False)
    names = [span["name"] for chunk in decoded for span in chunk]
    assert "from-processor" in names


def test_before_fork_does_not_block_other_python_threads():
    # The fork barrier waits for the worker's in-flight agent POST, and it has to. The agent here is a
    # Python handler in this process, so a barrier that keeps the GIL starves the thread that answers
    # the POST, and every Python thread then stalls until the request times out.
    started = threading.Event()

    class _SlowHandler(_CaptureHandler):
        def _capture(self):
            started.set()
            time.sleep(1.0)
            super()._capture()

    ticks = [0]
    stop = threading.Event()

    def tick():
        while not stop.is_set():
            ticks[0] += 1
            time.sleep(0.005)

    server, server_thread = _start_capture_server(_SlowHandler)
    ticker = threading.Thread(target=tick)
    ticker.start()
    try:
        with buffer_writer(server.port, api_version="v0.4") as writer:
            runtime = get_native_runtime()
            writer.write([_finished_span()])
            # A blocking flush_queue() would wait out the whole export itself, so nothing would still
            # be in flight by the time before_fork() runs. Trigger-only force_flush() keeps the export
            # running in the background, which is what this test needs to observe.
            writer._buffer.force_flush()
            assert started.wait(10.0), "the agent never received the export"
            ticks_before = ticks[0]
            start = time.monotonic()
            runtime.before_fork()
            elapsed = time.monotonic() - start
            try:
                assert elapsed > 0.2, "the barrier returned before the export was in flight"
                # A GIL-holding barrier lets the ticker through at most once, as it races into the
                # native call. A detached one wakes it every 5ms of the wait.
                assert ticks[0] - ticks_before > 10, "before_fork stopped every other Python thread"
            finally:
                runtime.after_fork_parent()
    finally:
        stop.set()
        ticker.join()
        server.shutdown()
        server_thread.join()


def test_shutdown_with_a_zero_timeout_reports_success():
    # tracer.shutdown(timeout=0) reaches shutdown(0). The worker is taken either way, so a reported
    # failure makes the caller retry a shutdown that already happened, and the retry does nothing.
    # An agent that never answers must not turn this into a long hang: each step of shutdown gives up
    # only its own (zero) share of the budget.
    with black_hole_server() as port:
        writer = NativeTraceBuffer("http://%s:%s" % (_HOST, port))
        writer.write([_finished_span()])
        start = time.monotonic()
        writer._buffer.shutdown(0)
        elapsed = time.monotonic() - start
    assert elapsed < 2.0, elapsed


def test_shutdown_is_bounded_when_the_agent_never_answers():
    # The exporter's own shutdown is reserved a minimum slice regardless of how the flush and the
    # worker stop spend theirs, so a black-hole agent must not make shutdown run away past its budget.
    with black_hole_server() as port:
        writer = NativeTraceBuffer("http://%s:%s" % (_HOST, port), api_version="v0.4")
        writer.write([_finished_span()])
        start = time.monotonic()
        writer.stop(1.0)
        elapsed = time.monotonic() - start
    # The budget is spent in three sequential network-bound steps, each of which can run to its own
    # share of 1.0s before giving up, so some slack above the nominal total is expected.
    assert elapsed < 3.0, elapsed


def _recorded_exporter_settings(construct, with_args=False):
    """Return the exporter-builder methods that `construct` calls, in order.

    When `with_args` is true, each entry also holds the arguments of the call.

    Neither writer exposes the exporter configuration it built, and the native builder keeps no record
    of the calls it received, so a recording stand-in is the only way to compare the two writers.
    """
    calls: list = []
    real_builder = writer_module.native.TraceExporterBuilder

    class _Recorder:
        def __init__(self):
            self._inner = real_builder()

        def __getattr__(self, name):
            inner = getattr(self._inner, name)

            def record(*args, **kwargs):
                calls.append((name, args, sorted(kwargs.items())) if with_args else name)
                inner(*args, **kwargs)
                # The native setters return the builder for chaining. Hand back the recorder, or the
                # rest of a chain records nothing.
                return self

            return record

        def build(self, runtime):
            # Building for real would spawn the exporter's own workers, and nothing here reclaims them.
            calls.append(("build", (), []) if with_args else "build")
            return None

    with (
        mock.patch.object(writer_module.native, "TraceExporterBuilder", _Recorder),
        mock.patch.object(writer_module.native, "TraceBuffer", lambda *args, **kwargs: None),
    ):
        construct()
    return calls


def test_both_native_writers_configure_the_exporter_identically(monkeypatch):
    # Every exporter setting the buffer leaves out changes behaviour the moment the flag goes on. The
    # OTLP auth headers are the sharpest case: without them the vendor endpoint rejects the payload,
    # and nothing on the tracer side says why.
    monkeypatch.setattr(otel_config.exporter, "TRACES_HEADERS", "api-key=trace-secret")
    monkeypatch.setattr(otel_config.exporter, "METRICS_HEADERS", "api-key=metric-secret")
    monkeypatch.setattr(config, "_otel_trace_semantics_enabled", True)
    monkeypatch.setattr(config, "_telemetry_enabled", True)

    def construct(writer_class):
        return writer_class(
            intake_url="http://%s:%s" % (_HOST, _BUFFER_PORT),
            api_version="v0.4",
            otlp_endpoint="http://%s:%s/v1/traces" % (_HOST, _BUFFER_PORT),
            otlp_metrics_endpoint="http://%s:%s/v1/metrics" % (_HOST, _BUFFER_PORT),
        )

    buffer_calls = _recorded_exporter_settings(lambda: construct(NativeTraceBuffer))
    writer_calls = _recorded_exporter_settings(lambda: construct(NativeWriter))

    # NativeWriter ends on build(); the buffer hands the builder to libdatadog instead.
    assert buffer_calls == [call for call in writer_calls if call != "build"]


def test_both_native_writers_pass_the_same_exporter_settings(monkeypatch):
    # Equal method names do not prove equal settings. A writer can pass a different value to the same
    # setter. The api_key and the agentless stats endpoint apply to NativeWriter only, so the test
    # passes only the arguments that both constructors accept.
    monkeypatch.setattr(otel_config.exporter, "TRACES_HEADERS", "api-key=trace-secret")
    monkeypatch.setattr(otel_config.exporter, "METRICS_HEADERS", "api-key=metric-secret")
    monkeypatch.setattr(config, "_otel_trace_semantics_enabled", True)
    monkeypatch.setattr(config, "_telemetry_enabled", True)
    monkeypatch.setattr(config, "_health_metrics_enabled", True)
    monkeypatch.setattr(config, "_report_hostname", True)
    monkeypatch.setattr(config, "service", "a-service")
    monkeypatch.setattr(config, "env", "an-env")
    monkeypatch.setattr(config, "version", "1.2.3")
    monkeypatch.setattr(config, "tags", {"team": "apm", "env": "an-env"})
    monkeypatch.setattr(config, "_trace_stats_additional_tags", ["region"])
    monkeypatch.setattr(writer_module.process_tags, "process_tags", "entrypoint.name:test")
    monkeypatch.setattr(writer_module.sys, "platform", "linux")

    def construct(writer_class):
        return writer_class(
            intake_url="http://%s:%s" % (_HOST, _BUFFER_PORT),
            api_version="v0.4",
            compute_stats_enabled=True,
            client_side_stats_obfuscation=True,
            test_session_token="a-token",
            otlp_endpoint="http://%s:%s/v1/traces" % (_HOST, _BUFFER_PORT),
            otlp_metrics_endpoint="http://%s:%s/v1/metrics" % (_HOST, _BUFFER_PORT),
        )

    buffer_calls = _recorded_exporter_settings(lambda: construct(NativeTraceBuffer), with_args=True)
    writer_calls = _recorded_exporter_settings(lambda: construct(NativeWriter), with_args=True)

    assert buffer_calls == [call for call in writer_calls if call[0] != "build"]


def test_buffer_reports_through_the_shared_telemetry_worker(monkeypatch):
    # The buffer must report through the tracer's telemetry worker instead of a second one, and must
    # follow that worker when the telemetry writer rebuilds it.
    from ddtrace.internal.telemetry import telemetry_writer

    shared_worker, rebuilt_worker = object(), object()
    monkeypatch.setattr(config, "_telemetry_enabled", True)
    monkeypatch.setattr(telemetry_writer, "_get_shared_worker", lambda: shared_worker)
    subscribers: list = []
    monkeypatch.setattr(telemetry_writer, "_subscribe_worker_changes", subscribers.append)
    # _build_exporter_builder enables a dedicated telemetry worker on Linux only.
    monkeypatch.setattr(writer_module.sys, "platform", "linux")

    builder = mock.Mock()
    for method_name in [
        "set_language",
        "set_language_version",
        "set_language_interpreter",
        "set_tracer_version",
        "set_git_commit_sha",
        "set_runtime_id",
        "set_client_computed_top_level",
        "set_input_format",
    ]:
        getattr(builder, method_name).return_value = builder
    buffers: list = []

    def make_buffer(*args, **kwargs):
        buffers.append(mock.Mock())
        return buffers[-1]

    monkeypatch.setattr(writer_module.native, "TraceExporterBuilder", lambda: builder)
    monkeypatch.setattr(writer_module.native, "TraceBuffer", make_buffer)

    writer = NativeTraceBuffer("http://%s:%s" % (_HOST, _BUFFER_PORT))
    builder.enable_telemetry.assert_not_called()
    buffers[0].set_telemetry_handle.assert_called_once_with(shared_worker)

    writer.set_test_session_token("token")
    buffers[1].set_telemetry_handle.assert_called_once_with(shared_worker)

    on_worker_changed = subscribers[-1]
    on_worker_changed(rebuilt_worker)
    buffers[1].set_telemetry_handle.assert_called_with(rebuilt_worker)
    on_worker_changed(None)
    buffers[1].set_telemetry_handle.assert_called_with(None)
    # The replaced buffer is shut down, so a worker change must not reach it.
    assert buffers[0].set_telemetry_handle.call_count == 1


def test_a_buffer_rebuild_follows_a_telemetry_worker_change_during_the_build(monkeypatch):
    # The telemetry writer can announce a rebuilt worker while set_test_session_token builds the
    # replacement buffer. The announcement then reaches the buffer that is about to be discarded, and
    # the replacement must not stay on the worker that the build started with.
    from ddtrace.internal.telemetry import telemetry_writer

    shared_worker, rebuilt_worker = object(), object()
    monkeypatch.setattr(config, "_telemetry_enabled", True)
    monkeypatch.setattr(telemetry_writer, "_get_shared_worker", lambda: shared_worker)
    subscribers: list = []
    monkeypatch.setattr(telemetry_writer, "_subscribe_worker_changes", subscribers.append)
    monkeypatch.setattr(writer_module, "_build_exporter_builder", lambda **kwargs: mock.Mock())
    buffers: list = []

    def make_buffer(*args, **kwargs):
        if buffers:
            subscribers[-1](rebuilt_worker)
        buffers.append(mock.Mock())
        return buffers[-1]

    monkeypatch.setattr(writer_module.native, "TraceBuffer", make_buffer)

    writer = NativeTraceBuffer("http://%s:%s" % (_HOST, _BUFFER_PORT))
    writer.set_test_session_token("token")
    assert writer._buffer is buffers[1]
    buffers[1].set_telemetry_handle.assert_called_with(rebuilt_worker)


@pytest.mark.subprocess(env={"DD_INSTRUMENTATION_TELEMETRY_ENABLED": "true"})
def test_buffer_spawns_no_telemetry_worker_of_its_own():
    from ddtrace.internal.native_runtime import get_native_runtime
    from ddtrace.internal.telemetry import telemetry_writer
    from ddtrace.internal.writer import NativeTraceBuffer
    from ddtrace.internal.writer import NativeWriter

    runtime = get_native_runtime()
    shared_worker = telemetry_writer._get_shared_worker()
    assert shared_worker is not None

    before = runtime.debug().count("WorkerEntry")
    writer = NativeWriter("http://localhost:9126")
    writer_workers = runtime.debug().count("WorkerEntry") - before
    writer.shutdown_exporter()

    before = runtime.debug().count("WorkerEntry")
    subscribers_before = len(telemetry_writer._worker_subscribers)
    buffer = NativeTraceBuffer("http://localhost:9126")
    # The buffer runs one worker more than NativeWriter: the one that drains the buffer.
    assert runtime.debug().count("WorkerEntry") - before == writer_workers + 1
    assert len(telemetry_writer._worker_subscribers) == subscribers_before + 1

    buffer._buffer.set_telemetry_handle(None)
    buffer._buffer.set_telemetry_handle(shared_worker)
    buffer.stop(1.0)
    # After the shutdown the exporter is gone. A late worker change must not raise.
    buffer._buffer.set_telemetry_handle(shared_worker)


def test_an_unsupported_api_version_falls_back_to_a_supported_one(agent):
    # An unsupported DD_TRACE_API_VERSION reaches set_input_format, which raises, so with the flag on
    # the tracer crashed on import instead of warning.
    with buffer_writer(agent.port, api_version="v0.3") as writer:
        assert writer._api_version == "v0.5"


class _StubBuffer:
    """Stands in for the native buffer, to hand write() a chosen agent response body."""

    def __init__(self, body=None, reason=None):
        self._body = body
        self._reason = reason

    def take_agent_response(self):
        body, self._body = self._body, None
        return body

    def write(self, spans, dd_origin=None):
        return self._reason

    def force_flush(self):
        pass

    def shutdown(self, timeout_ns):
        pass


@pytest.mark.parametrize("body", ["5", "true", '{"rate_by_service": 5}'])
def test_a_malformed_agent_response_does_not_raise_into_user_code(body, agent):
    # write() runs from Span.finish(). A 2xx body that parses to a scalar fails the membership test,
    # and a rate_by_service that is not a mapping fails inside the sampler callback.
    with buffer_writer(agent.port, response_callback=lambda response: dict(response.rate_by_service)) as writer:
        native_buffer = writer._buffer
        writer._buffer = _StubBuffer(body=body)
        try:
            writer.write([_finished_span()])
        finally:
            writer._buffer = native_buffer


def test_writes_after_stop_do_not_warn_once_per_trace(agent):
    # Spans keep finishing while the tracer tears down, and every one of those writes reports
    # AlreadyClosed. A warning each buries the shutdown itself in the log.
    writer = NativeTraceBuffer("http://%s:%s" % (_HOST, agent.port))
    writer.stop(1.0)
    with mock.patch.object(writer_module.log, "warning") as warning:
        writer.write([_finished_span()])
    assert not warning.called, warning.call_args_list


def test_a_timed_out_synchronous_export_is_not_reported_as_lost_spans(monkeypatch):
    # libdatadog keeps the chunk when the wait for the export report times out, and sends it on the
    # next flush. Calling that dropped spans sends the reader hunting for data loss that never happened.
    monkeypatch.setattr(agent_config, "trace_agent_timeout_seconds", 0.1)

    class _SlowHandler(_CaptureHandler):
        def _capture(self):
            time.sleep(1.0)
            super()._capture()

    with capture_server(_SlowHandler) as server:
        with buffer_writer(server.port, api_version="v0.4", sync_mode=True) as writer:
            with mock.patch.object(writer_module, "_safelog") as safelog:
                writer.write([_finished_span()])

    logged = [call.args for call in safelog.call_args_list]
    assert len(logged) == 1, logged
    _, message, reason = logged[0]
    assert "TimedOut" in reason, reason
    assert "dropped" not in message, message


@contextlib.contextmanager
def _native_writer(url, **kwargs):
    writer = NativeWriter(url, **kwargs)
    try:
        yield writer
    finally:
        try:
            writer.stop(3.0)
        except ServiceStatusError:
            # A sync-mode writer never starts its periodic thread, but its exporter still needs a
            # shutdown.
            writer.on_shutdown()


@contextlib.contextmanager
def _native_buffer(url, **kwargs):
    writer = NativeTraceBuffer(url, **kwargs)
    try:
        yield writer
    finally:
        writer.stop(3.0)


@pytest.mark.parametrize("writer_class", [NativeWriter, NativeTraceBuffer])
def test_windows_rejects_the_v05_api_version(writer_class, monkeypatch):
    # Both writers send through the same native exporter, so the v0.5 incompatibility on Windows
    # applies to both. See https://github.com/DataDog/dd-trace-py/issues/4829.
    monkeypatch.setattr(writer_module.sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="4829"):
        writer_class("http://%s:%s" % (_HOST, _BUFFER_PORT), api_version="v0.5")


@pytest.mark.parametrize("make_writer", [_native_writer, _native_buffer], ids=["NativeWriter", "NativeTraceBuffer"])
def test_an_unsupported_api_version_on_windows_does_not_stop_the_tracer(make_writer, monkeypatch):
    # The fallback for an unsupported version runs after the Windows check, so one bad setting cannot
    # abort tracer init.
    monkeypatch.setattr(writer_module.sys, "platform", "win32")
    with make_writer("http://%s:%s" % (_HOST, _BUFFER_PORT), api_version="v0.6") as writer:
        assert writer._api_version in ("v0.4", "v0.5")


@pytest.mark.parametrize("setting", ["agent_timeout", "writer_interval"])
@pytest.mark.parametrize("value", [-1.0, 0.0])
def test_a_non_positive_duration_setting_keeps_a_usable_buffer(setting, value, monkeypatch, agent):
    # A negative duration fails the conversion to an unsigned native duration and aborts tracer init.
    # A zero synchronous-export timeout reports every write as TimedOut.
    if setting == "agent_timeout":
        monkeypatch.setattr(agent_config, "trace_agent_timeout_seconds", value)
    else:
        monkeypatch.setattr(config, "_trace_writer_interval_seconds", value)
    real_buffer = writer_module.native.TraceBuffer
    passed = {}

    def capture(*args, **kwargs):
        passed.update(kwargs)
        return real_buffer(*args, **kwargs)

    monkeypatch.setattr(writer_module.native, "TraceBuffer", capture)
    with buffer_writer(agent.port, sync_mode=True):
        pass
    assert passed["synchronous_export_timeout_ns"] > 0
    assert passed["max_flush_interval_ns"] is None or passed["max_flush_interval_ns"] > 0


class _ClosedOnWrite(_StubBuffer):
    """Stand in for a buffer that a rebuild replaced and closed while write() held it."""

    def __init__(self, writer, replacement):
        super().__init__()
        self._writer = writer
        self._replacement = replacement

    def write(self, spans, dd_origin=None):
        self._writer._buffer = self._replacement
        return "AlreadyClosed"


class _RecordingBuffer(_StubBuffer):
    def __init__(self):
        super().__init__()
        self.writes = []

    def write(self, spans, dd_origin=None):
        self.writes.append(spans)
        return None


def test_a_write_that_races_a_buffer_rebuild_reaches_the_new_buffer(agent):
    # set_test_session_token closes the old buffer right after it installs the new one. A write that
    # still holds the old buffer must not lose its trace.
    with buffer_writer(agent.port) as writer:
        native_buffer = writer._buffer
        replacement = _RecordingBuffer()
        writer._buffer = _ClosedOnWrite(writer, replacement)
        try:
            with mock.patch.object(writer_module, "log") as log:
                writer.write([_finished_span()])
        finally:
            writer._buffer = native_buffer
    assert len(replacement.writes) == 1
    log.warning.assert_not_called()


# The tests below run against both writers, because the backend reads the wire data of either writer
# the same way. The two writers must agree on every value.
_BOTH_WRITERS = pytest.mark.parametrize(
    "make_writer",
    [pytest.param(_native_writer, id="NativeWriter"), pytest.param(_native_buffer, id="NativeTraceBuffer")],
)


def _trace(trace_id, big=False):
    """Return a small trace, or a trace larger than the 1 MiB buffer of the keep-rate tests."""
    name, size = ("a" * 5000, 150) if big else ("op", 5)
    return [Span(name=name, trace_id=trace_id, span_id=j + 1, parent_id=j or None) for j in range(size)]


def _write_overflow(writer):
    """Write a trace larger than the buffer, then a small trace. Each writer drops one of the two.

    NativeWriter drops the large trace, because the trace does not fit. libdatadog accepts the large
    trace and drops the small one, because its buffer is then over the limit. Until the next flush,
    libdatadog drops every further trace.
    """
    big, small = _trace(100, big=True), _trace(101)
    writer.write(big)
    writer.write(small)
    return big[0]._get_numeric_attribute("_dd.tracer_kr"), small[0]._get_numeric_attribute("_dd.tracer_kr")


def _keep_rate(writer, trace):
    writer.write(trace)
    return trace[0]._get_numeric_attribute("_dd.tracer_kr")


@pytest.fixture
def keep_rate_config(monkeypatch):
    monkeypatch.setattr(config, "_trace_writer_buffer_size", 1 << 20)
    # libdatadog flushes when the buffer passes this size, which would empty the buffer before it
    # drops a trace.
    monkeypatch.setattr(config, "_trace_writer_payload_size", 8 << 20)
    # The periodic thread of NativeWriter must not add a bucket in the middle of a test that adds
    # them with flush_queue().
    monkeypatch.setattr(config, "_trace_writer_interval_seconds", 3600.0)


@_BOTH_WRITERS
def test_keep_rate_follows_drops_and_recovers(make_writer, keep_rate_config, agent):
    with make_writer("http://%s:%s" % (_HOST, agent.port), api_version="v0.4") as writer:
        # No drops yet, so every trace is kept.
        assert [_keep_rate(writer, _trace(i)) for i in range(1, 5)] == [1.0] * 4
        writer.flush_queue()

        # Buckets: 0 of 4 dropped.
        assert _write_overflow(writer) == (1.0, 1.0)
        writer.flush_queue()
        # Buckets: 0 of 4 dropped, 1 of 2 dropped.
        assert [_keep_rate(writer, _trace(i)) for i in range(5, 7)] == [pytest.approx(5 / 6)] * 2
        writer.flush_queue()
        # Buckets: 0 of 4 dropped, 1 of 2 dropped, 0 of 2 dropped.
        assert _keep_rate(writer, _trace(7)) == pytest.approx(7 / 8)
        writer.flush_queue()

        # Empty windows push the drops out of the 10-bucket average.
        for _ in range(10):
            writer.flush_queue()
        assert _keep_rate(writer, _trace(8)) == 1.0


@_BOTH_WRITERS
def test_keep_rate_updates_without_an_explicit_flush(make_writer, keep_rate_config, monkeypatch, agent):
    # In steady state nothing calls flush_queue(). The keep rate must still move once per writer
    # interval.
    interval = 0.1
    monkeypatch.setattr(config, "_trace_writer_interval_seconds", interval)
    with make_writer("http://%s:%s" % (_HOST, agent.port), api_version="v0.4") as writer:
        _write_overflow(writer)
        time.sleep(3 * interval)
        # Buckets: 1 of 2 dropped, then empty buckets.
        assert _keep_rate(writer, _trace(1)) == pytest.approx(0.5)

        # After a full window of idle intervals, the drops age out.
        time.sleep(15 * interval)
        assert _keep_rate(writer, _trace(2)) == 1.0


def _drop_one_trace(writer):
    """Make the writer drop one trace for a full buffer, and return the keep rate of that trace.

    In sync mode, libdatadog exports each write before the next write starts, so a single thread
    cannot fill its buffer. A stub buffer reports the drop instead.
    """
    if isinstance(writer, NativeWriter):
        return _keep_rate(writer, _trace(100, big=True))
    native_buffer = writer._buffer
    writer._buffer = _StubBuffer(reason="BatchFull(BatchFullError { spans_dropped: 5 })")
    try:
        return _keep_rate(writer, _trace(100))
    finally:
        writer._buffer = native_buffer


@_BOTH_WRITERS
def test_keep_rate_in_sync_mode_updates_on_every_write(make_writer, keep_rate_config, agent):
    with make_writer("http://%s:%s" % (_HOST, agent.port), api_version="v0.4", sync_mode=True) as writer:
        assert _drop_one_trace(writer) == 1.0
        # Buckets: 1 of 1 dropped.
        assert _keep_rate(writer, _trace(1)) == 0.0
        # Buckets: 1 of 1 dropped, 0 of 1 dropped.
        assert _keep_rate(writer, _trace(2)) == pytest.approx(0.5)


class _GatedSMA(writer_module.SimpleMovingAverage):
    """Hold the first set() open until the test releases it, and record how many calls overlap."""

    def __init__(self, size):
        super().__init__(size)
        self.guard = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.first_entered = threading.Event()
        self.release = threading.Event()

    def set(self, count, total):
        with self.guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.first_entered.set()
        self.release.wait(5.0)
        super().set(count, total)
        with self.guard:
            self.active -= 1


def test_concurrent_writes_update_the_keep_rate_one_at_a_time(agent):
    # SimpleMovingAverage.set() is a read-modify-write of several fields. Two overlapping calls
    # corrupt the running sums for the rest of the process.
    with buffer_writer(agent.port, sync_mode=True) as writer:
        native_buffer = writer._buffer
        writer._buffer = _StubBuffer()
        sma = writer._drop_sma = _GatedSMA(writer_module.DEFAULT_SMA_WINDOW)
        try:
            first = threading.Thread(target=writer.write, args=([_finished_span(trace_id=1)],))
            first.start()
            assert sma.first_entered.wait(5.0)
            second = threading.Thread(target=writer.write, args=([_finished_span(trace_id=2)],))
            second.start()
            deadline = time.monotonic() + 5.0
            while second.is_alive() and sma.active < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            sma.release.set()
            first.join(5.0)
            second.join(5.0)
        finally:
            writer._buffer = native_buffer
        assert sma.max_active == 1


class _UpdateBeforeAcquireLock:
    """Complete a keep rate update for another thread just before the caller takes the lock."""

    def __init__(self, writer):
        self._writer = writer
        self._lock = threading.Lock()

    def acquire(self, blocking=True):
        self._writer._drop_rate_updated_at = time.monotonic()
        return self._lock.acquire(blocking)

    def release(self):
        self._lock.release()


def test_a_write_skips_the_keep_rate_update_that_another_thread_completed(monkeypatch, agent):
    # write() checks the interval before it takes the lock. A second update in the same interval
    # adds an extra bucket and ages drops out of the average too early.
    monkeypatch.setattr(config, "_trace_writer_interval_seconds", 3600.0)
    with buffer_writer(agent.port) as writer:
        native_buffer = writer._buffer
        writer._buffer = _StubBuffer()
        writer._drop_rate_updated_at = time.monotonic() - 7200.0
        writer._drop_rate_lock = _UpdateBeforeAcquireLock(writer)
        sma = writer._drop_sma = mock.Mock(wraps=writer._drop_sma)
        try:
            writer.write([_finished_span()])
        finally:
            writer._buffer = native_buffer
        assert sma.set.call_count == 0


class _TotalSMA(writer_module.SimpleMovingAverage):
    def __init__(self, size):
        super().__init__(size)
        self.total = 0

    def set(self, count, total):
        self.total += total
        super().set(count, total)


class _UpdateDuringIncrement(NativeTraceBuffer):
    """Run a keep rate update on another thread while write() is part way through its increment."""

    def __init__(self, *args, **kwargs):
        self._accepted = 0
        self.updater = None
        super().__init__(*args, **kwargs)

    @property
    def _accepted_traces(self):
        value = self._accepted
        if self.updater is None and value > 0:
            self.updater = threading.Thread(target=self._set_drop_rate)
            self.updater.start()
            # An update that does not wait for write() completes inside this window.
            self.updater.join(0.5)
        return value

    @_accepted_traces.setter
    def _accepted_traces(self, value):
        self._accepted = value


def test_a_keep_rate_update_during_a_write_counts_each_trace_once(monkeypatch, agent):
    # write() increments the trace counters while a keep rate update reads and resets them. An
    # increment that straddles the reset counts the whole bucket twice.
    monkeypatch.setattr(config, "_trace_writer_interval_seconds", 3600.0)
    writer = _UpdateDuringIncrement("http://%s:%s" % (_HOST, agent.port))
    native_buffer = writer._buffer
    writer._buffer = _StubBuffer()
    sma = writer._drop_sma = _TotalSMA(writer_module.DEFAULT_SMA_WINDOW)
    try:
        writer.write([_finished_span(trace_id=1)])
        writer.write([_finished_span(trace_id=2)])
        writer.updater.join(5.0)
        writer._set_drop_rate()
    finally:
        writer._buffer = native_buffer
        writer.stop(3.0)
    assert sma.total == 2


@_BOTH_WRITERS
def test_write_sets_the_otlp_trace_context(make_writer, agent):
    # libdatadog maps these two attributes to the OTLP trace state and flags, and it reads only the
    # span attributes. The writer must copy them from the live Context before the span leaves Python.
    trace_id = 0xFFF972474538EFFF
    root = Span("root", trace_id=trace_id, span_id=1)
    root.context._publish_sampling_decision(1, 0.1, True)
    child = Span("child", trace_id=trace_id, span_id=2, parent_id=1, context=root.context)
    root.finish()
    child.finish()

    with make_writer(
        "http://%s:%s" % (_HOST, agent.port), otlp_endpoint="http://%s:%s/v1/traces" % (_HOST, agent.port)
    ) as writer:
        writer.write([root, child])

    for span in (root, child):
        assert span.get_metric("_sampling_priority_v1") == 1
        assert "ot=rv:ef284ace7a91e1;th:e6666666666668" in span.get_tag("tracestate")
