"""
Test thread-safety and fork behavior of IAST.

The Initializer class uses object pooling for performance.
This test verifies:
1. Concurrent access from multiple threads doesn't cause issues
2. IAST is properly disabled in forked child processes to prevent segfaults
"""

import os
import sys
import threading

import pytest

from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._iast_request_context_base import _iast_start_request
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject


def test_initializer_thread_safety():
    """Test that Initializer's object pools are thread-safe."""

    errors = []
    results = []

    def worker(worker_id, iterations=100):
        """Worker function that creates tainted objects."""
        try:
            oce.reconfigure()
            _iast_start_request()

            for i in range(iterations):
                # This exercises the object pool allocation/deallocation
                data = taint_pyobject(
                    f"worker_{worker_id}_data_{i}",
                    f"worker_{worker_id}_source_{i}",
                    f"worker_{worker_id}_value_{i}",
                    OriginType.PARAMETER,
                )
                # Let the string go out of scope to trigger deallocation
                del data

            results.append({"worker_id": worker_id, "success": True})

        except Exception as e:
            errors.append({"worker_id": worker_id, "error": str(e)})

    # Create multiple threads that will concurrently access the object pools
    threads = []
    num_threads = 10

    for i in range(num_threads):
        thread = threading.Thread(target=worker, args=(i,))
        threads.append(thread)
        thread.start()

    # Wait for all threads to complete
    for thread in threads:
        thread.join(timeout=30)

    # Verify all threads completed successfully
    assert len(errors) == 0, f"Thread errors occurred: {errors}"
    assert len(results) == num_threads, f"Expected {num_threads} results, got {len(results)}"


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
def test_initializer_reset_after_fork():
    """
    Test that IAST is disabled in forked child processes.

    When a process forks, the native extension state cannot be safely used.
    The fork handler disables IAST in the child to prevent segmentation faults.
    This test verifies that the child process doesn't crash when IAST is disabled.
    """

    # Setup parent with IAST enabled
    oce.reconfigure()
    _iast_start_request()
    _ = taint_pyobject(
        "parent_data",
        "parent_source",
        "parent_value",
        OriginType.PARAMETER,
    )

    pid = os.fork()

    if pid == 0:
        # Child process - IAST should be disabled by fork handler
        try:
            # These calls should not crash even though IAST is disabled
            oce.reconfigure()
            _iast_start_request()

            # taint_pyobject should be a no-op since IAST is disabled
            _ = taint_pyobject(
                "child_data",
                "child_source",
                "child_value",
                OriginType.COOKIE,
            )

            # If we got here without segfault, IAST was safely disabled
            os._exit(0)

        except Exception as e:
            print(f"Child error: {e}", file=sys.stderr)
            os._exit(1)

    else:
        # Parent process
        _, status = os.waitpid(pid, 0)
        assert status == 0, "Child process should exit successfully with IAST disabled"
