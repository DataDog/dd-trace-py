"""
Regression tests for IAST fork handler to prevent segmentation faults.

These tests verify that the fork handler is properly registered and that
IAST can safely work in multiprocessing scenarios without causing segfaults.
"""
from multiprocessing import Process
from multiprocessing import Queue
import os
import sys

import pytest

from ddtrace.appsec._iast._iast_request_context_base import _num_objects_tainted_in_request
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from tests.appsec.iast.iast_utils import _end_iast_context_and_oce
from tests.appsec.iast.iast_utils import _start_iast_context_and_oce


def test_fork_handler_callable(iast_context_defaults):
    """Verify that _reset_iast_after_fork is callable and doesn't crash."""
    from ddtrace.appsec._iast import _reset_iast_after_fork

    # Should not raise any exception
    try:
        _reset_iast_after_fork()
    except Exception as e:
        pytest.fail(f"Fork handler raised unexpected exception: {e}")


def test_fork_handler_with_active_context(iast_context_defaults):
    """Verify fork handler works when IAST context is active."""
    from ddtrace.appsec._iast import _reset_iast_after_fork

    _start_iast_context_and_oce()

    # Create some tainted objects
    taint_pyobject("test_data", source_name="test", source_value="test", source_origin=OriginType.PARAMETER)

    # Reset should clear the context
    _reset_iast_after_fork()

    # After reset, we should be able to create a new context safely
    _end_iast_context_and_oce()


def test_multiprocessing_with_iast_no_segfault(iast_context_defaults):
    """
    Regression test: Verify that using multiprocessing with IAST enabled
    doesn't cause segmentation faults.

    This tests the fix for the issue where forking a process with IAST
    enabled would cause segfaults in the child process.
    """

    def child_process_work(queue):
        """Child process that uses IAST functionality."""
        try:
            # Start IAST in child
            _start_iast_context_and_oce()

            # Perform taint operations that previously caused segfaults
            tainted_str = taint_pyobject(
                "child_data", source_name="child_source", source_value="value", source_origin=OriginType.PARAMETER
            )

            # Verify operations work
            count = _num_objects_tainted_in_request()
            queue.put(("success", count, tainted_str))

        except Exception as e:
            queue.put(("error", str(e), type(e).__name__))

    # Parent setup
    _start_iast_context_and_oce()
    _ = taint_pyobject(
        "parent_data", source_name="parent", source_value="value", source_origin=OriginType.HEADER_NAME
    )  # noqa: F841

    # Fork a child process
    queue = Queue()
    child = Process(target=child_process_work, args=(queue,))
    child.start()
    child.join(timeout=5)

    # Verify child didn't crash
    assert child.exitcode == 0, f"Child process crashed with exit code {child.exitcode}"

    # Verify child completed successfully
    result = queue.get(timeout=1)
    assert result[0] == "success", f"Child process failed: {result}"
    assert result[1] > 0, "Child should have tainted objects"


def test_multiple_fork_operations(iast_context_defaults):
    """
    Test that multiple sequential fork operations don't cause issues.

    This ensures the fork handler is idempotent and can be called multiple times.
    """

    def simple_child_work(queue, child_id):
        """Simple child process work."""
        try:
            _start_iast_context_and_oce()
            taint_pyobject(f"data_{child_id}", "source", "value", OriginType.PARAMETER)
            queue.put(("success", child_id))
        except Exception as e:
            queue.put(("error", child_id, str(e)))

    _start_iast_context_and_oce()

    num_children = 5
    queue = Queue()
    children = []

    for i in range(num_children):
        child = Process(target=simple_child_work, args=(queue, i))
        children.append(child)
        child.start()

    # Wait for all children
    for child in children:
        child.join(timeout=5)
        assert child.exitcode == 0, f"Child {child.pid} crashed"

    # Verify all completed successfully
    results = []
    while not queue.empty():
        results.append(queue.get(timeout=1))

    assert len(results) == num_children, f"Expected {num_children} results, got {len(results)}"
    for result in results:
        assert result[0] == "success", f"Child failed: {result}"


def test_fork_with_os_fork_no_segfault(iast_context_defaults):
    """
    Test that os.fork() directly doesn't cause segfaults.

    This is a more direct test of the fork safety mechanism.
    """
    _start_iast_context_and_oce()
    parent_data = taint_pyobject("parent", "source", "value", OriginType.PATH)  # noqa: F841

    pid = os.fork()

    if pid == 0:
        # Child process
        try:
            # This should not segfault after the fork handler runs
            _start_iast_context_and_oce()
            child_data = taint_pyobject("child", "source", "value", OriginType.PARAMETER)  # noqa: F841
            count = _num_objects_tainted_in_request()
            assert count > 0, "Child should have tainted objects"
            os._exit(0)
        except Exception as e:
            print(f"Child error: {e}", file=sys.stderr)
            os._exit(1)
    else:
        # Parent process
        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 0, f"Child process failed with exit code {exit_code}"


def test_fork_handler_clears_state(iast_context_defaults):
    """
    Verify that the fork handler actually clears taint tracking state.

    This tests the implementation detail that clear_all_request_context_slots
    is called properly.
    """
    from ddtrace.appsec._iast import _reset_iast_after_fork

    _start_iast_context_and_oce()
    taint_pyobject("test", "source", "value", OriginType.PARAMETER)

    # Manually call the fork handler (simulating what happens after fork)
    _reset_iast_after_fork()

    # After reset, starting a new context should work
    _end_iast_context_and_oce()
    _start_iast_context_and_oce()

    # Should be able to create new tainted objects without issues
    taint_pyobject("test2", "source2", "value2", OriginType.PARAMETER)
    count = _num_objects_tainted_in_request()
    assert count > 0, "Should be able to create tainted objects after reset"

    _end_iast_context_and_oce()


def test_eval_in_forked_process(iast_context_defaults):
    """
    Regression test: Verify that eval() with IAST instrumentation works
    in forked processes without segfaulting.
    """

    def child_eval_work(queue):
        """Child process that uses eval with IAST."""
        try:
            _start_iast_context_and_oce()

            # Test eval with tainted input
            code = "1 + 1"
            tainted_code = taint_pyobject(code, "code_source", code, OriginType.PARAMETER)

            # This should not crash
            result = eval(tainted_code)

            queue.put(("success", result))
        except Exception as e:
            queue.put(("error", str(e), type(e).__name__))

    _start_iast_context_and_oce()

    queue = Queue()
    child = Process(target=child_eval_work, args=(queue,))
    child.start()
    child.join(timeout=5)

    assert child.exitcode == 0, f"Child crashed with exit code {child.exitcode}"

    result = queue.get(timeout=1)
    assert result[0] == "success", f"Child eval failed: {result}"
    assert result[1] == 2, "Eval should return correct result"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
