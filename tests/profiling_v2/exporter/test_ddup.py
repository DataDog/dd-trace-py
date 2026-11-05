import sys

import pytest

from ddtrace.internal.datadog.profiling import ddup


class MockSpan:
    """Mock span object for testing"""

    def __init__(self, span_id=None, local_root=None):
        if span_id is not None:
            self.span_id = span_id
        if local_root is not None:
            self._local_root = local_root


class MockLocalRoot:
    """Mock local root span object for testing"""

    def __init__(self, span_id=None, span_type=None):
        if span_id is not None:
            self.span_id = span_id
        if span_type is not None:
            self.span_type = span_type


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_libdd_available():
    """
    Tests that the libdd module can be loaded
    """

    assert ddup.is_available


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_ddup_start():
    """
    Tests that the the libdatadog exporter can be enabled
    """

    try:
        ddup.config(
            env="my_env",
            service="my_service",
            version="my_version",
            tags={},
        )
        ddup.start()
    except Exception as e:
        pytest.fail(str(e))


@pytest.mark.skipif(not ddup.is_available, reason="ddup not available")
@pytest.mark.subprocess()
def test_upload_does_not_block_sample_flush():
    """
    Regression test: an upload that waits for the agent must not keep the profile lock.
    Before the fix, flush_sample() in another thread waited for that lock, with the GIL held, until the
    agent answered or the upload timed out.
    """
    from email.parser import BytesParser
    from email.policy import default
    from http.server import BaseHTTPRequestHandler
    from http.server import HTTPServer
    import io
    import json
    import threading
    import time

    import zstandard

    from ddtrace.internal.datadog.profiling import ddup
    from tests.profiling.collector.pprof_utils import get_sample_type_index
    from tests.profiling.collector.pprof_utils import pprof_pb2

    request_received = threading.Event()
    send_response = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                body = bytearray()
                while True:
                    size = int(self.rfile.readline().split(b";", 1)[0], 16)
                    if not size:
                        while self.rfile.readline().strip():
                            pass
                        break
                    body.extend(self.rfile.read(size))
                    self.rfile.read(2)
            else:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            header = ("Content-Type: %s\r\n\r\n" % self.headers["Content-Type"]).encode()
            message = BytesParser(policy=default).parsebytes(header + body)
            parts = {part.get_filename(): part.get_payload(decode=True) for part in message.iter_parts()}
            counts = json.loads(parts["event.json"])["endpoint_counts"]
            profile = pprof_pb2.Profile()
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(parts["profile.pprof"])) as reader:
                profile.ParseFromString(reader.read())
            wall = get_sample_type_index(profile, "wall-time")
            requests.append((self.headers["Host"], counts, [sample.value[wall] for sample in profile.sample]))
            request_received.set()
            # Keep the upload in flight until the test has added a sample
            send_response.wait(timeout=30)
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format, *args):
            pass

    class Tracer:
        def __init__(self, host, port, label):
            self.host = "%s:%s" % (host, port)
            self.label = label
            self.prepared = threading.Event()
            self._endpoint_call_counter_span_processor = self

        @property
        def agent_trace_url(self):
            self.prepared.set()
            return "http://" + self.host

        def reset(self):
            return {self.label: 1}, {}

    def add_sample(value):
        sample = ddup.SampleHandle()
        sample.push_walltime(value, 1)
        sample.flush_sample()

    upload_timeout = 3.0

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        port = server.server_port
        tracer = Tracer("127.0.0.1", port, "first")

        ddup.config(
            env="my_env", service="my_service", version="my_version", tags={}, timeout=int(upload_timeout * 1000)
        )
        ddup.start()
        add_sample(11)

        upload_thread = threading.Thread(target=ddup.upload, kwargs=dict(tracer=tracer), daemon=True)
        upload_thread.start()
        assert request_received.wait(timeout=10), "the agent did not receive the profile"

        # The upload now waits for the response of the agent
        start = time.monotonic()
        add_sample(22)
        elapsed = time.monotonic() - start
        upload_in_flight = upload_thread.is_alive()

        # Queue two more callers behind the first upload. Each must keep its Agent and endpoint counts.
        pending = []
        for caller in (Tracer("localhost", port, "second"), Tracer("127.0.0.1", port, "third")):
            thread = threading.Thread(target=ddup.upload, kwargs=dict(tracer=caller), daemon=True)
            thread.start()
            pending.append(thread)
            assert caller.prepared.wait(timeout=10)
        send_response.set()
        for thread in [upload_thread] + pending:
            thread.join(timeout=10)
            assert not thread.is_alive()
        server.shutdown()
        server_thread.join()

    assert upload_in_flight, "the upload ended before the sample was added, so the test proved nothing"
    assert elapsed < upload_timeout / 2, "flush_sample() waited %.3f s for the upload" % elapsed
    assert requests[0] == (tracer.host, {"first": 1}, [11])
    assert len(requests) == 3
    assert {host: counts for host, counts, _ in requests[1:]} == {
        "localhost:%d" % port: {"second": 1},
        tracer.host: {"third": 1},
    }
    assert sorted(value for _, _, samples in requests[1:] for value in samples) == [22]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.skipif(not ddup.is_available, reason="ddup not available")
@pytest.mark.subprocess(err=None)
def test_upload_during_exit_does_not_abort():
    """
    Regression test: an upload that runs in an atexit handler of the C library must not abort the process.
    uWSGI finalizes Python in such a handler, and the profiler then uploads the last profile. At that time,
    exit() has already destroyed the thread-local data that libdatadog needs to send the request.
    """
    import ctypes

    from ddtrace.internal.datadog.profiling import ddup

    ddup.config(env="my_env", service="my_service", version="my_version", tags={})
    ddup.start()

    libc = ctypes.CDLL(None)

    @ctypes.CFUNCTYPE(None, ctypes.c_void_p)
    def upload_at_exit(_arg):
        ddup.upload()

    # atexit() is not a dynamic symbol of glibc, so use the function that atexit() calls
    libc.__cxa_atexit(upload_at_exit, None, None)
    # Call exit() of the C library directly, so that the handler runs before Python is finalized
    libc.exit(0)


@pytest.mark.subprocess(
    env=dict(
        DD_TAGS="hello:world",
        DD_PROFILING_TAGS="foo:bar,hello:python",
    )
)
def test_tags_propagated():
    import sys
    from unittest.mock import Mock

    sys.modules["ddtrace.internal.datadog.profiling.ddup"] = Mock()

    from ddtrace.profiling.profiler import Profiler  # noqa: I001
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.settings.profiling import config

    # DD_PROFILING_TAGS should override DD_TAGS
    assert config.tags["hello"] == "python"
    assert config.tags["foo"] == "bar"

    # When Profiler is instantiated and libdd is enabled, it should call ddup.config
    Profiler()

    ddup.config.assert_called()

    tags = ddup.config.call_args.kwargs["tags"]

    # Profiler could add tags, so check that tags is a superset of config.tags
    for k, v in config.tags.items():
        assert tags[k] == v


@pytest.mark.skipif(not ddup.is_available, reason="ddup not available")
def test_push_span_without_span_id():
    """
    Test that push_span handles span objects without span_id attribute gracefully.
    This can happen when profiling collector encounters mock span objects in tests.
    Regression test for issue where AttributeError was raised when accessing span.span_id.
    """

    # Create a sample handle
    handle = ddup.SampleHandle()

    # Test 1: Span without span_id attribute
    span_no_id = MockSpan()
    # Should not raise AttributeError
    handle.push_span(span_no_id)

    # Test 2: Span without _local_root attribute
    span_no_local_root = MockSpan(span_id=12345)
    # Should not raise AttributeError
    handle.push_span(span_no_local_root)

    # Test 3: Span with _local_root but local_root without span_id
    local_root_no_id = MockLocalRoot()
    span_with_incomplete_root = MockSpan(span_id=12345, local_root=local_root_no_id)
    # Should not raise AttributeError
    handle.push_span(span_with_incomplete_root)

    # Test 4: Span with _local_root but local_root without span_type
    local_root_no_type = MockLocalRoot(span_id=67890)
    span_with_root_no_type = MockSpan(span_id=12345, local_root=local_root_no_type)
    # Should not raise AttributeError
    handle.push_span(span_with_root_no_type)

    # Test 5: Complete span (should work as before)
    complete_local_root = MockLocalRoot(span_id=67890, span_type="web")
    complete_span = MockSpan(span_id=12345, local_root=complete_local_root)
    # Should not raise AttributeError
    handle.push_span(complete_span)

    # Test 6: None span (should handle gracefully)
    handle.push_span(None)
