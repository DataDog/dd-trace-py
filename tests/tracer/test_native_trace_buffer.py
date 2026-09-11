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
    """Collects every request body the tracer sends, so a test can decode the wire payload."""

    payloads: list = []

    def _capture(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            _CaptureHandler.payloads.append(self.rfile.read(length))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_PUT(self):
        self._capture()

    def do_POST(self):
        self._capture()


@contextlib.contextmanager
def capture_server(port=_BUFFER_PORT):
    _CaptureHandler.payloads = []
    server, thread = _make_server(port, _CaptureHandler)
    try:
        yield _CaptureHandler
    finally:
        server.shutdown()
        thread.join()


@contextlib.contextmanager
def black_hole_server(port):
    """Accept connections but never answer, so a wait against it ends only on its own timeout."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
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
        yield
    finally:
        stop.set()
        thread.join()
        for conn in conns:
            conn.close()
        sock.close()


@contextlib.contextmanager
def buffer_writer(port=_BUFFER_PORT, **kwargs):
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
        with buffer_writer(api_version="v0.4") as writer:
            writer.write([_finished_span(name="smoke")])
            writer.flush_queue()
            body = _wait_for_payload(handler)

    assert body, "the buffer never delivered a payload"
    decoded = msgpack.unpackb(body, raw=False, strict_map_key=False)
    names = [span["name"] for chunk in decoded for span in chunk]
    assert "smoke" in names


def test_write_stamps_the_client_keep_rate():
    # The backend scales its dropped-trace estimate by _dd.tracer_kr, and the snapshot suite asserts
    # it on every root span.
    span = _finished_span()
    with buffer_writer() as writer:
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
def test_write_never_raises_into_user_code(sabotage):
    # write() runs from Span.finish() inside a `with tracer.trace(...)` block, so an exception here
    # escapes into application code.
    span = Span(name="op", trace_id=1, span_id=1)
    sabotage(span)
    span.finish()
    with buffer_writer() as writer:
        writer.write([span])


def test_write_reports_unreadable_spans_without_raising():
    # A non-SpanData item cannot be projected. write() must report it rather than raise, and the
    # writer logs the reason.
    with buffer_writer() as writer:
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

    port = _BUFFER_PORT + 5
    _CaptureHandler.payloads = []
    server, server_thread = _make_server(port, _SlowHandler)
    try:
        with buffer_writer(port=port, api_version="v0.4") as writer:
            writer.write([_finished_span(name="blocking")])
            writer.flush_queue()
            assert _CaptureHandler.payloads, "flush_queue returned before the export was delivered"
    finally:
        server.shutdown()
        server_thread.join()


def test_flush_queue_leaves_the_buffer_usable():
    # flush() must not shut anything down: a later write and flush have to reach the agent too. Each
    # flush delivers its own msgpack document, so decode with an Unpacker instead of assuming the
    # accumulated bytes form a single document.
    with capture_server() as handler:
        with buffer_writer(api_version="v0.4") as writer:
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


def test_flush_queue_on_an_idle_buffer_returns_promptly():
    # An idle buffer has nothing to wait on, so the wait libdatadog performs must return immediately
    # rather than block for the full agent timeout.
    with buffer_writer() as writer:
        start = time.monotonic()
        writer.flush_queue()
        elapsed = time.monotonic() - start
    assert elapsed < 1.0, elapsed


def test_flush_queue_timeout_is_not_reported_as_lost_spans(monkeypatch):
    # Same contract as write()'s own TimedOut handling, but through the blocking flush_queue() path.
    # _flush_timeout_ns() floors the wait at 1 second, so the handler has to run well past that for
    # the flush to time out before the export finishes.
    monkeypatch.setattr(agent_config, "trace_agent_timeout_seconds", 0.1)
    port = _BUFFER_PORT + 6

    class _SlowHandler(_CaptureHandler):
        def _capture(self):
            time.sleep(2.0)
            super()._capture()

    _CaptureHandler.payloads = []
    server, server_thread = _make_server(port, _SlowHandler)
    try:
        with buffer_writer(port=port, api_version="v0.4") as writer:
            writer.write([_finished_span()])
            with mock.patch.object(writer_module, "_safelog") as safelog:
                writer.flush_queue()
    finally:
        server.shutdown()
        server_thread.join()

    logged = [call.args for call in safelog.call_args_list]
    assert len(logged) == 1, logged
    _, message, reason = logged[0]
    assert "TimedOut" in reason, reason
    assert "dropped" not in message, message
    assert "failed" not in message, message


def test_flush_queue_with_a_non_positive_timeout_does_not_raise(monkeypatch):
    # A configured timeout of 0 must not become a native zero-timeout wait: libdatadog reports that as
    # TimedOut for a flush that a moment later still succeeds, which would raise on every flush.
    monkeypatch.setattr(agent_config, "trace_agent_timeout_seconds", 0)
    with buffer_writer() as writer:
        writer.write([_finished_span()])
        writer.flush_queue(raise_exc=True)


def test_flush_queue_inside_the_fork_window_does_not_block():
    # A blocking flush issued while _paused is true would wait on a condvar nothing can notify, since
    # no runtime exists in that window to service it. flush_queue() must fall back to a fire-and-forget
    # flush instead.
    runtime = get_native_runtime()
    with buffer_writer() as writer:
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


def test_shutdown_reclaims_the_exporters_own_workers():
    # The buffer owns one worker, and the exporter it consumed owns more: agent-info, telemetry and
    # dogstatsd, plus the OTLP stats exporter. Only TraceExporter::shutdown reclaims those, and it
    # consumes the exporter, so a buffer that merely holds it inside its Export impl leaks them on
    # every stop() and every recreate().
    runtime = get_native_runtime()
    before = _registered_workers(runtime)
    writer = NativeTraceBuffer("http://%s:%s" % (_HOST, _BUFFER_PORT), api_version="v0.4")
    try:
        assert _registered_workers(runtime) > before, "the buffer registered no worker"
    finally:
        writer.stop(3.0)
    assert _registered_workers(runtime) == before


def test_shutdown_reclaims_the_exporter_around_an_export_in_flight():
    # The shutdown must reclaim the exporter only after the buffer's worker has stopped, because that
    # worker may still be inside a send and the send holds the exporter for its duration. Reaching for
    # the exporter first finds it busy and gives up on it, which leaks its workers exactly when a
    # shutdown races a slow agent. The handler here answers slowly, so the export is in flight when
    # stop() runs.
    started = threading.Event()

    class _SlowHandler(_CaptureHandler):
        def _capture(self):
            started.set()
            time.sleep(0.5)
            super()._capture()

    runtime = get_native_runtime()
    port = _BUFFER_PORT + 4
    _CaptureHandler.payloads = []
    server, server_thread = _make_server(port, _SlowHandler)
    try:
        before = _registered_workers(runtime)
        writer = NativeTraceBuffer("http://%s:%s" % (_HOST, port), api_version="v0.4")
        writer.write([_finished_span(name="in-flight")])
        writer.flush_queue()
        assert started.wait(10.0), "the agent never received the export"
        writer.stop(5.0)
        assert _registered_workers(runtime) == before
        body = _wait_for_payload(_CaptureHandler)
    finally:
        server.shutdown()
        server_thread.join()

    assert body, "the export in flight at shutdown was lost"
    decoded = msgpack.unpackb(body, raw=False, strict_map_key=False)
    names = [span["name"] for chunk in decoded for span in chunk]
    assert "in-flight" in names


def test_shutdown_is_idempotent():
    writer = NativeTraceBuffer("http://%s:%s" % (_HOST, _BUFFER_PORT))
    writer.stop(1.0)
    # A second stop must not raise: SpanAggregator.shutdown and atexit can both reach it.
    writer.stop(1.0)


def test_recreate_returns_a_fresh_writer():
    with buffer_writer() as writer:
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
            with buffer_writer(api_version="v0.4") as writer:
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


def test_recreate_shuts_the_replaced_buffer_down():
    # libdatadog reclaims a buffer's worker on shutdown only, and tracer.configure() reaches recreate()
    # on every ASM remote-config update, so a buffer that is merely dropped leaks a worker per call. A
    # shut-down buffer refuses further spans, which is what makes the shutdown observable from here.
    with buffer_writer(api_version="v0.4") as writer:
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

    server, thread = _make_server(_BUFFER_PORT + 1, _RateHandler)
    try:
        with buffer_writer(
            port=_BUFFER_PORT + 1,
            api_version="v0.4",
            response_callback=received.append,
        ) as writer:
            writer.write([_finished_span()])
            writer.flush_queue()
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not received:
                time.sleep(0.05)
                writer.flush_queue()
    finally:
        server.shutdown()
        thread.join()

    assert received, "the agent response never reached the callback"
    assert received[0].rate_by_service == {"service:test,env:": 0.5}


def test_concurrent_writes_do_not_corrupt_the_buffer():
    # send_chunk takes the buffer's state mutex, so concurrent application threads are safe. This
    # pins that, because the buffer is reached from every request thread.
    with capture_server() as handler:
        with buffer_writer(api_version="v0.4") as writer:

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
        with buffer_writer(api_version="v0.5") as writer:
            writer.write([_finished_span(name="smoke-v05")])
            writer.flush_queue()
            body = _wait_for_payload(handler)

    assert body, "the buffer never delivered a v0.5 payload"
    # A v0.5 payload is [string_table, traces], where every span field is an index into the table.
    string_table, traces = msgpack.unpackb(body, raw=False, strict_map_key=False)
    assert "smoke-v05" in string_table
    assert sum(len(chunk) for chunk in traces) > 0


def test_v05_strips_the_span_link_flags_sentinel():
    # Bit 31 of a link's flags marks "flags present" for the v0.4 wire form. libdatadog's v0.5
    # conversion copies flags into meta["_dd.span_links"] without stripping it, so the wire span must
    # arrive already masked. An unmasked value shows up as 2147483649 rather than 1.
    span = Span(name="linked", trace_id=1, span_id=1)
    span._set_link(
        trace_id=0x1234567890ABCDEF1234567890ABCDEF,
        span_id=0x1122334455667788,
        flags=1,
    )
    span.finish()

    with capture_server() as handler:
        with buffer_writer(api_version="v0.5") as writer:
            writer.write([span])
            writer.flush_queue()
            body = _wait_for_payload(handler)

    assert body
    string_table, _ = msgpack.unpackb(body, raw=False, strict_map_key=False)
    links_json = [s for s in string_table if "span_id" in s and "flags" in s]
    assert links_json, "v0.5 output carried no JSON-encoded span links"
    assert '"flags":1' in links_json[0].replace(" ", ""), links_json[0]
    assert "2147483649" not in links_json[0], "the flags sentinel leaked into the v0.5 JSON"


def test_v05_carries_span_events():
    # v0.5 has no span_events wire field, so libdatadog JSON-encodes them into meta["events"].
    span = Span(name="evented", trace_id=1, span_id=1)
    span._add_event("my_event", attributes={"k": "v"}, time_unix_nano=1700000000000000000)
    span.finish()

    with capture_server() as handler:
        with buffer_writer(api_version="v0.5") as writer:
            writer.write([span])
            writer.flush_queue()
            body = _wait_for_payload(handler)

    assert body
    string_table, _ = msgpack.unpackb(body, raw=False, strict_map_key=False)
    assert any("my_event" in s for s in string_table), "the span event never reached the payload"


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
        with buffer_writer(api_version="v0.4") as writer:
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

    port = _BUFFER_PORT + 2
    _CaptureHandler.payloads = []
    server, server_thread = _make_server(port, _SlowHandler)
    ticker = threading.Thread(target=tick)
    ticker.start()
    try:
        with buffer_writer(port=port, api_version="v0.4") as writer:
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
    port = _BUFFER_PORT + 7
    with black_hole_server(port):
        writer = NativeTraceBuffer("http://%s:%s" % (_HOST, port))
        writer.write([_finished_span()])
        start = time.monotonic()
        writer._buffer.shutdown(0)
        elapsed = time.monotonic() - start
    assert elapsed < 2.0, elapsed


def test_shutdown_is_bounded_when_the_agent_never_answers():
    # The exporter's own shutdown is reserved a minimum slice regardless of how the flush and the
    # worker stop spend theirs, so a black-hole agent must not make shutdown run away past its budget.
    port = _BUFFER_PORT + 8
    with black_hole_server(port):
        writer = NativeTraceBuffer("http://%s:%s" % (_HOST, port), api_version="v0.4")
        writer.write([_finished_span()])
        start = time.monotonic()
        writer.stop(1.0)
        elapsed = time.monotonic() - start
    # The budget is spent in three sequential network-bound steps, each of which can run to its own
    # share of 1.0s before giving up, so some slack above the nominal total is expected.
    assert elapsed < 3.0, elapsed


def _recorded_exporter_settings(construct):
    """Return the exporter-builder methods that `construct` calls, in order.

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
                calls.append(name)
                inner(*args, **kwargs)
                # The native setters return the builder for chaining. Hand back the recorder, or the
                # rest of a chain records nothing.
                return self

            return record

        def build(self, runtime):
            # Building for real would spawn the exporter's own workers, and nothing here reclaims them.
            calls.append("build")
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


def test_an_unsupported_api_version_falls_back_to_a_supported_one():
    # An unsupported DD_TRACE_API_VERSION reaches set_input_format, which raises, so with the flag on
    # the tracer crashed on import instead of warning.
    with buffer_writer(api_version="v0.3") as writer:
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
def test_a_malformed_agent_response_does_not_raise_into_user_code(body):
    # write() runs from Span.finish(). A 2xx body that parses to a scalar fails the membership test,
    # and a rate_by_service that is not a mapping fails inside the sampler callback.
    with buffer_writer(response_callback=lambda response: dict(response.rate_by_service)) as writer:
        native_buffer = writer._buffer
        writer._buffer = _StubBuffer(body=body)
        try:
            writer.write([_finished_span()])
        finally:
            writer._buffer = native_buffer


def test_writes_after_stop_do_not_warn_once_per_trace():
    # Spans keep finishing while the tracer tears down, and every one of those writes reports
    # AlreadyClosed. A warning each buries the shutdown itself in the log.
    writer = NativeTraceBuffer("http://%s:%s" % (_HOST, _BUFFER_PORT))
    writer.stop(1.0)
    with mock.patch.object(writer_module.log, "warning") as warning:
        writer.write([_finished_span()])
    assert not warning.called, warning.call_args_list


def test_a_timed_out_synchronous_export_is_not_reported_as_lost_spans(monkeypatch):
    # libdatadog keeps the chunk when the wait for the export report times out, and sends it on the
    # next flush. Calling that dropped spans sends the reader hunting for data loss that never happened.
    monkeypatch.setattr(agent_config, "trace_agent_timeout_seconds", 0.1)
    port = _BUFFER_PORT + 3

    class _SlowHandler(_CaptureHandler):
        def _capture(self):
            time.sleep(1.0)
            super()._capture()

    _CaptureHandler.payloads = []
    server, server_thread = _make_server(port, _SlowHandler)
    try:
        with buffer_writer(port=port, api_version="v0.4", sync_mode=True) as writer:
            with mock.patch.object(writer_module, "_safelog") as safelog:
                writer.write([_finished_span()])
    finally:
        server.shutdown()
        server_thread.join()

    logged = [call.args for call in safelog.call_args_list]
    assert len(logged) == 1, logged
    _, message, reason = logged[0]
    assert "TimedOut" in reason, reason
    assert "dropped" not in message, message
