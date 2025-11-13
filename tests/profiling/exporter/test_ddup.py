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


@pytest.mark.subprocess(env=dict(DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED="True"))
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


@pytest.mark.skipif(not ddup.is_available, reason="ddup not available")
def test_concurrent_upload_sequence_numbers(tmp_path):
    """Test that concurrent uploads produce unique, sequential file sequence numbers.

    This is a regression test for a race condition in the C++ Uploader class where
    multiple threads creating Uploader objects concurrently could result in files
    with duplicate or non-sequential sequence numbers.

    The bug occurred because:
    1. Thread A creates Uploader -> upload_seq becomes 1
    2. Thread B creates Uploader -> upload_seq becomes 2
    3. Thread B exports file -> uses current upload_seq value (2)
    4. Thread A exports file -> uses current upload_seq value (2, not 1!)

    This resulted in duplicate sequence numbers or missing sequences.

    The fix captures the sequence number atomically in the Uploader constructor
    (instance_upload_seq = ++upload_seq) so each instance has its own unique number.
    """
    import glob
    import os
    import threading
    import time

    from ddtrace.profiling.collector.threading import ThreadingLockCollector

    test_name = "test_concurrent_upload"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    # Configure ddup
    ddup.config(
        env="test",
        service=test_name,
        version="test_version",
        output_filename=pprof_prefix,
    )
    ddup.start()

    num_threads = 10
    num_uploads_per_thread = 5
    barrier = threading.Barrier(num_threads)  # Synchronize thread start for maximum contention

    def upload_worker():
        """Each thread collects samples and uploads multiple times."""
        # Wait for all threads to be ready
        barrier.wait()

        for _ in range(num_uploads_per_thread):
            # Collect some samples (using lock collector to generate profile data)
            with ThreadingLockCollector(capture_pct=100):
                lock = threading.Lock()
                with lock:
                    time.sleep(0.001)  # Small amount of work

            # Upload immediately to create race condition
            ddup.upload()

    # Start threads
    threads = []
    for _ in range(num_threads):
        t = threading.Thread(target=upload_worker)
        threads.append(t)
        t.start()

    # Wait for all threads to complete
    for t in threads:
        t.join()

    # Analyze the created files
    files = glob.glob(output_filename + ".*")

    # Extract sequence numbers from filenames
    # Format: <prefix>.<pid>.<seq>
    sequence_numbers = []
    for f in files:
        seq = int(f.rsplit(".", 1)[-1])
        sequence_numbers.append(seq)

    sequence_numbers.sort()

    print(f"\nCreated {len(files)} files")
    print(f"Sequence numbers: {sequence_numbers}")

    # Check for issues
    expected_count = num_threads * num_uploads_per_thread

    # Issue 1: Missing files (duplicates overwrite each other)
    assert len(files) == expected_count, (
        f"Expected {expected_count} files, but found {len(files)}. "
        f"This suggests duplicate sequence numbers caused file overwrites!"
    )

    # Issue 2: Duplicate sequence numbers
    assert len(sequence_numbers) == len(
        set(sequence_numbers)
    ), f"Duplicate sequence numbers found! Sequences: {sequence_numbers}"

    # Issue 3: Gaps in sequence numbers
    # Sequences should be continuous (no gaps)
    for i in range(len(sequence_numbers) - 1):
        assert (
            sequence_numbers[i + 1] == sequence_numbers[i] + 1
        ), f"Gap in sequence numbers: {sequence_numbers[i]} -> {sequence_numbers[i + 1]}"

    # Cleanup
    for f in files:
        try:
            os.remove(f)
        except Exception as e:
            print(f"Error removing file {f}: {e}")
