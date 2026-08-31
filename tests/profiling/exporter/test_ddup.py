import sys
from typing import cast

import pytest

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.trace import Span


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


@pytest.mark.subprocess(
    env=dict(
        DD_TAGS="hello:world",
        DD_PROFILING_TAGS="foo:bar,hello:python",
    )
)
def test_tags_propagated():
    import sys
    from unittest.mock import Mock

    mock_ddup = Mock()
    sys.modules["ddtrace.internal.datadog.profiling.ddup"] = mock_ddup

    from ddtrace.profiling.profiler import Profiler  # noqa: I001
    from ddtrace.internal.settings.profiling import config

    # DD_PROFILING_TAGS should override DD_TAGS
    assert config.tags["hello"] == "python"
    assert config.tags["foo"] == "bar"

    # When Profiler is instantiated and libdd is enabled, it should call ddup.config
    Profiler()

    mock_ddup.config.assert_called()

    tags = mock_ddup.config.call_args.kwargs["tags"]

    # Profiler could add tags, so check that tags is a superset of config.tags
    for k, v in config.tags.items():
        assert tags[k] == v


@pytest.mark.subprocess()
def test_process_tags_propagated():
    import sys
    from unittest.mock import Mock

    sys.modules["ddtrace.internal.datadog.profiling.ddup"] = Mock()

    from ddtrace.profiling.profiler import Profiler  # noqa: I001
    from ddtrace.internal.datadog.profiling import ddup

    # When Profiler is instantiated and libdd is enabled, it should call ddup.config
    Profiler()

    ddup.config.assert_called()

    assert "process_tags" in ddup.config.call_args.kwargs


@pytest.mark.subprocess()
def test_upload_via_native_writes_pprof_and_info():
    """End-to-end check that ddup.upload() with use_native_uploader=True actually drives
    _upload_via_native(): serializes a real profile, resolves the lazy ProfileUploader import,
    and dumps a pprof/metadata/info file -- as opposed to only exercising the Rust
    ProfileUploader class directly (which never touches this code path).
    """
    import glob
    import json
    import logging
    import tempfile

    import pytest

    import ddtrace
    from ddtrace.internal.datadog.profiling import ddup

    if not ddup.is_available:
        pytest.skip("ddup not available")

    try:
        from ddtrace.internal.native._native import ProfileUploader  # noqa: F401
    except ImportError:
        pytest.skip("ProfileUploader is only built with the profiling Cargo feature")

    errors = []

    class _CapturingHandler(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                errors.append(record.getMessage())

    logging.getLogger("ddtrace.internal.datadog.profiling.ddup._ddup").addHandler(_CapturingHandler())

    tmp_dir = tempfile.mkdtemp()
    output_base = f"{tmp_dir}/e2e-profile"

    ddup.config(
        env="my_env",
        service="my_service",
        version="my_version",
        tags={},
        output_filename=output_base,
        use_native_uploader=True,
    )
    ddup.set_profiler_settings_json('{"application": {"env": "my_env"}}')
    ddup.start()

    handle = ddup.SampleHandle()
    handle.push_walltime(1_000_000, 1)
    handle.push_frame("my_function", "my_file.py", 0, 42)
    handle.flush_sample()

    ddup.upload(ddtrace.tracer, start_ns=0)

    assert not errors, f"native upload path logged errors: {errors}"

    pprof_matches = glob.glob(f"{output_base}.*.*.pprof")
    assert len(pprof_matches) == 1
    base = pprof_matches[0][: -len(".pprof")]
    assert len(open(pprof_matches[0], "rb").read()) > 0

    info_path = f"{base}.info.json"
    with open(info_path) as f:
        info = json.load(f)
    assert info == {"application": {"env": "my_env"}}


@pytest.mark.subprocess()
def test_upload_via_native_sends_tags_and_endpoint_stats():
    """End-to-end check that _upload_via_native() actually sends the tag-parity list (service,
    runtime-id, process_id, profiler_version, ...) and endpoint call-count stats to the exporter,
    by pointing the uploader at a file:// URL, which makes libdatadog dump the raw multipart
    request body instead of making a network call, and inspecting the dumped bytes.

    output_filename (used by the sibling pprof/metadata/info test above) short-circuits
    send_blocking() before it ever touches the exporter or tags, so it can't cover this.
    """
    import tempfile

    import pytest

    from ddtrace.internal.datadog.profiling import ddup

    if not ddup.is_available:
        pytest.skip("ddup not available")

    try:
        from ddtrace.internal.native._native import ProfileUploader  # noqa: F401
    except ImportError:
        pytest.skip("ProfileUploader is only built with the profiling Cargo feature")

    class _FakeEndpointCounter:
        def reset(self):
            return {"GET /foo": 3}, {}

    class _FakeTracer:
        _endpoint_call_counter_span_processor = _FakeEndpointCounter()

    tmp_dir = tempfile.mkdtemp()
    dump_path = f"{tmp_dir}/upload_dump.http"
    fake_tracer = _FakeTracer()
    fake_tracer.agent_trace_url = f"file://{dump_path}"

    ddup.config(
        env="my_env",
        service="my_service",
        version="my_version",
        tags={},
        use_native_uploader=True,
    )
    ddup.start()

    handle = ddup.SampleHandle()
    handle.push_walltime(1_000_000, 1)
    handle.push_frame("my_function", "my_file.py", 0, 42)
    handle.flush_sample()

    ddup.upload(fake_tracer, start_ns=0)

    dumped = open(dump_path, "rb").read()
    assert b"service:my_service" in dumped
    assert b"env:my_env" in dumped
    assert b"version:my_version" in dumped
    assert b"language:python" in dumped
    assert b"runtime-id:" in dumped
    assert b"process_id:" in dumped
    assert b"profiler_version:" in dumped
    assert b"endpoint_counts" in dumped
    assert b"GET /foo" in dumped


@pytest.mark.skipif(not ddup.is_available, reason="ddup not available")
def test_push_span_without_span_id():
    """
    Test that push_span handles span objects without span_id attribute gracefully.
    This can happen when profiling collector encounters mock span objects in tests.
    Regression test for issue where AttributeError was raised when accessing span.span_id.
    """

    ddup.config(
        env="my_env",
        service="my_service",
        version="my_version",
        tags={},
    )
    ddup.start()

    # Create a sample handle
    handle = ddup.SampleHandle()

    # Test 1: Span without span_id attribute
    span_no_id = cast(Span, MockSpan())
    # Should not raise AttributeError
    handle.push_span(span_no_id)

    # Test 2: Span without _local_root attribute
    span_no_local_root = cast(Span, MockSpan(span_id=12345))
    # Should not raise AttributeError
    handle.push_span(cast(Span, span_no_local_root))

    # Test 3: Span with _local_root but local_root without span_id
    local_root_no_id = MockLocalRoot()
    span_with_incomplete_root = cast(Span, MockSpan(span_id=12345, local_root=local_root_no_id))
    # Should not raise AttributeError
    handle.push_span(span_with_incomplete_root)

    # Test 4: Span with _local_root but local_root without span_type
    local_root_no_type = MockLocalRoot(span_id=67890)
    span_with_root_no_type = cast(Span, MockSpan(span_id=12345, local_root=local_root_no_type))
    # Should not raise AttributeError
    handle.push_span(span_with_root_no_type)

    # Test 5: Complete span (should work as before)
    complete_local_root = MockLocalRoot(span_id=67890, span_type="web")
    complete_span = cast(Span, MockSpan(span_id=12345, local_root=complete_local_root))
    # Should not raise AttributeError
    handle.push_span(complete_span)

    # Test 6: None span (should handle gracefully)
    handle.push_span(None)

    ddup.upload()
