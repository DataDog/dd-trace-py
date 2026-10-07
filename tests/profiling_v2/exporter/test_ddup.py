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


@pytest.mark.subprocess()
def test_upload_does_not_block_sample_flush():
    """
    Regression test: an upload that waits for the agent must not keep the profile lock.
    Before the fix, flush_sample() in another thread waited for that lock, with the GIL held, until the
    agent answered or the upload timed out.
    """
    from http.server import BaseHTTPRequestHandler
    from http.server import HTTPServer
    import threading
    import time

    from ddtrace.internal.datadog.profiling import ddup

    request_received = threading.Event()
    send_response = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            request_received.set()
            # Keep the upload in flight until the test has added a sample
            send_response.wait(timeout=30)
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format, *args):
            pass

    class EndpointProcessor:
        def reset(self):
            return {}, {}

    class Tracer:
        _endpoint_call_counter_span_processor = EndpointProcessor()

        def __init__(self, agent_trace_url):
            self.agent_trace_url = agent_trace_url

    def add_sample():
        sample = ddup.SampleHandle()
        sample.push_walltime(1, 1)
        sample.flush_sample()

    upload_timeout = 3.0

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        tracer = Tracer("http://127.0.0.1:%d" % server.server_address[1])

        ddup.config(
            env="my_env", service="my_service", version="my_version", tags={}, timeout=int(upload_timeout * 1000)
        )
        ddup.start()
        add_sample()

        upload_thread = threading.Thread(target=ddup.upload, kwargs=dict(tracer=tracer), daemon=True)
        upload_thread.start()
        assert request_received.wait(timeout=10), "the agent did not receive the profile"

        # The upload now waits for the response of the agent
        start = time.monotonic()
        add_sample()
        elapsed = time.monotonic() - start
        upload_in_flight = upload_thread.is_alive()

        send_response.set()
        upload_thread.join(timeout=10)
        server.shutdown()
        server_thread.join()

    assert upload_in_flight, "the upload ended before the sample was added, so the test proved nothing"
    assert elapsed < upload_timeout / 2, "flush_sample() waited %.3f s for the upload" % elapsed


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
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
