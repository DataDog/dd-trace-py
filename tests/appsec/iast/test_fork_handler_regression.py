"""
Regression tests for IAST fork handler to prevent segmentation faults.

These tests verify that IAST correctly handles two types of forks:

1. **Early forks (web workers)**: Forks that happen BEFORE IAST has any active state.
   These are safe - IAST remains enabled in the child and can initialize fresh.
   Example: gunicorn/uvicorn worker processes.

2. **Late forks (multiprocessing)**: Forks that happen AFTER IAST has active contexts.
   These inherit corrupted native state and must have IAST disabled in the child.
   Example: multiprocessing.Process with IAST already running.

The fork handler detects which type of fork occurred by checking for active contexts.
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
    """Verify that _reset_iast_after_fork is callable and disables IAST."""
    from ddtrace.appsec._iast import _disable_iast_after_fork
    from ddtrace.appsec._iast._taint_tracking import reset_native_state
    from ddtrace.internal.settings.asm import config as asm_config

    # Should not raise any exception
    try:
        original_state = asm_config._iast_enabled
        _disable_iast_after_fork()
        # Fork handler should disable IAST
        assert asm_config._iast_enabled is False, "IAST should be disabled after fork"
        # Restore for other tests - reinitialize native state for clean slate
        asm_config._iast_enabled = original_state
        reset_native_state()  # Reinitialize to clean state for next test
    except Exception as e:
        pytest.fail(f"Fork handler raised unexpected exception: {e}")


def test_fork_handler_with_active_context(iast_context_defaults):
    """Verify fork handler disables IAST and clears context when active."""
    from ddtrace.appsec._iast import _disable_iast_after_fork
    from ddtrace.appsec._iast._taint_tracking import is_tainted
    from ddtrace.appsec._iast._taint_tracking import reset_native_state
    from ddtrace.internal.settings.asm import config as asm_config

    _start_iast_context_and_oce()

    # Create some tainted objects
    tainted = taint_pyobject("test_data", source_name="test", source_value="test", source_origin=OriginType.PARAMETER)
    assert is_tainted(tainted), "Should be tainted before fork"

    # Reset simulates what happens after fork - IAST is disabled
    original_state = asm_config._iast_enabled
    _disable_iast_after_fork()

    # IAST should now be disabled
    assert asm_config._iast_enabled is False, "IAST should be disabled after fork"

    # After reset, we should be able to call these safely (they're no-ops now)
    _end_iast_context_and_oce()

    # Restore for other tests - reinitialize native state for clean slate
    asm_config._iast_enabled = original_state
    reset_native_state()  # Reinitialize to clean state for next test


@pytest.mark.skip(reason="multiprocessing fork doesn't work correctly in ddtrace-py 4.0")
def test_multiprocessing_with_iast_no_segfault(iast_context_defaults):
    """
    Regression test: Verify that late forks (multiprocessing) safely disable IAST.

    This simulates multiprocessing.Process forking AFTER IAST has active contexts.
    The fork handler should detect the active state and disable IAST in the child
    to prevent segmentation faults from corrupted native extension state.
    """
    import multiprocessing

    # Python 3.14+ defaults to 'forkserver' which requires pickling.
    # Force 'fork' method for this test since we use local functions.
    original_start_method = multiprocessing.get_start_method(allow_none=True)
    multiprocessing.set_start_method("fork", force=True)

    def child_process_work(queue):
        """Child process where IAST should be disabled."""
        try:
            from ddtrace.appsec._iast._taint_tracking import is_tainted
            from ddtrace.internal.settings.asm import config as asm_config

            # Start IAST in child (will be a no-op since IAST is disabled)
            _start_iast_context_and_oce()

            # IAST should be disabled in child
            is_enabled = asm_config._iast_enabled

            # Taint operations should be no-ops (not crash)
            tainted_str = taint_pyobject(
                "child_data", source_name="child_source", source_value="value", source_origin=OriginType.PARAMETER
            )

            # Since IAST is disabled, count should be 0 and object not tainted
            count = _num_objects_tainted_in_request()
            is_obj_tainted = is_tainted(tainted_str)

            queue.put(("success", is_enabled, count, is_obj_tainted))

        except Exception as e:
            queue.put(("error", str(e), type(e).__name__))

    # Parent setup - IAST works normally
    _start_iast_context_and_oce()
    _ = taint_pyobject("parent_data", source_name="parent", source_value="value", source_origin=OriginType.HEADER_NAME)  # noqa: F841

    # Fork a child process
    queue = Queue()
    child = Process(target=child_process_work, args=(queue,))
    child.start()
    child.join(timeout=5)

    # Verify child didn't crash (the main goal)
    assert child.exitcode == 0, f"Child process crashed with exit code {child.exitcode}"

    # Verify child completed successfully
    result = queue.get(timeout=1)
    assert result[0] == "success", f"Child process failed: {result}"
    assert result[1] is False, "IAST should be disabled in child"
    assert result[2] == 0, "Child should have 0 tainted objects (IAST disabled)"
    assert result[3] is False, "Objects should not be tainted in child (IAST disabled)"

    # Restore original start method
    if original_start_method:
        multiprocessing.set_start_method(original_start_method, force=True)


@pytest.mark.skip(reason="multiprocessing fork doesn't work correctly in ddtrace-py 4.0")
def test_multiple_fork_operations(iast_context_defaults):
    """
    Test that multiple sequential fork operations don't cause segfaults.

    Each child process should have IAST safely disabled by the fork handler,
    ensuring no crashes occur even with multiple forks.
    """
    import multiprocessing

    # Python 3.14+ defaults to 'forkserver' which requires pickling.
    # Force 'fork' method for this test since we use local functions.
    original_start_method = multiprocessing.get_start_method(allow_none=True)
    multiprocessing.set_start_method("fork", force=True)

    def simple_child_work(queue, child_id):
        """Simple child process work - IAST will be disabled."""
        try:
            from ddtrace.internal.settings.asm import config as asm_config

            # These should be safe no-ops since IAST is disabled
            _start_iast_context_and_oce()
            taint_pyobject(f"data_{child_id}", "source", "value", OriginType.PARAMETER)

            # Verify IAST is disabled
            is_enabled = asm_config._iast_enabled
            queue.put(("success", child_id, is_enabled))
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

    # Verify all completed successfully without IAST enabled
    results = []
    while not queue.empty():
        results.append(queue.get(timeout=1))

    assert len(results) == num_children, f"Expected {num_children} results, got {len(results)}"
    for result in results:
        assert result[0] == "success", f"Child failed: {result}"
        assert result[2] is False, f"IAST should be disabled in child {result[1]}"

    # Restore original start method
    if original_start_method:
        multiprocessing.set_start_method(original_start_method, force=True)


def test_fork_with_os_fork_no_segfault(iast_context_defaults):
    """
    Test that os.fork() directly doesn't cause segfaults.

    This is a direct test of fork safety - IAST is disabled in the child
    to prevent any native extension corruption issues.
    """
    from ddtrace.appsec._iast._taint_tracking import is_tainted

    _start_iast_context_and_oce()
    parent_data = taint_pyobject("parent", "source", "value", OriginType.PATH)
    assert is_tainted(parent_data), "Should be tainted in parent"

    pid = os.fork()

    if pid == 0:
        # Child process - IAST should be disabled
        try:
            from ddtrace.internal.settings.asm import config as asm_config

            # IAST should be disabled after fork
            if asm_config._iast_enabled:
                print("ERROR: IAST should be disabled in child", file=sys.stderr)
                os._exit(1)

            # These should not segfault (they're no-ops)
            _start_iast_context_and_oce()
            child_data = taint_pyobject("child", "source", "value", OriginType.PARAMETER)

            # Since IAST is disabled, nothing should be tainted
            count = _num_objects_tainted_in_request()
            if count != 0:
                print(f"ERROR: Expected 0 tainted objects, got {count}", file=sys.stderr)
                os._exit(1)

            if is_tainted(child_data):
                print("ERROR: Object should not be tainted (IAST disabled)", file=sys.stderr)
                os._exit(1)

            os._exit(0)
        except Exception as e:
            print(f"Child error: {e}", file=sys.stderr)
            os._exit(1)
    else:
        # Parent process
        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 0, f"Child process failed with exit code {exit_code}"
        # Clean up the manually started context to avoid leaving context slots occupied
        _end_iast_context_and_oce()


def test_fork_handler_clears_state(iast_context_defaults):
    """
    Verify that the fork handler disables IAST and clears state.

    The fork handler clears all taint tracking state and disables IAST
    to prevent segmentation faults from corrupted native extension state.
    """
    from ddtrace.appsec._iast import _disable_iast_after_fork
    from ddtrace.appsec._iast._taint_tracking import is_tainted
    from ddtrace.internal.settings.asm import config as asm_config

    _start_iast_context_and_oce()
    tainted = taint_pyobject("test", "source", "value", OriginType.PARAMETER)
    assert is_tainted(tainted), "Should be tainted before fork"

    # Manually call the fork handler (simulating what happens after fork)
    original_state = asm_config._iast_enabled
    _disable_iast_after_fork()

    # IAST should now be disabled
    assert asm_config._iast_enabled is False, "IAST should be disabled after fork"

    # After reset, these should be safe no-ops
    _end_iast_context_and_oce()
    _start_iast_context_and_oce()

    # taint_pyobject should be a no-op (IAST disabled)
    tainted2 = taint_pyobject("test2", "source2", "value2", OriginType.PARAMETER)
    count = _num_objects_tainted_in_request()
    assert count == 0, "Should have 0 tainted objects (IAST disabled)"
    assert not is_tainted(tainted2), "Should not be tainted (IAST disabled)"

    _end_iast_context_and_oce()

    # Restore for other tests
    asm_config._iast_enabled = original_state


@pytest.mark.skip(reason="multiprocessing fork doesn't work correctly in ddtrace-py 4.0")
def test_eval_in_forked_process(iast_context_defaults):
    """
    Regression test: Verify that eval() doesn't crash in forked processes.

    With IAST disabled in the child, eval() should work normally without
    any instrumentation or tainting.
    """
    import multiprocessing

    # Python 3.14+ defaults to 'forkserver' which requires pickling.
    # Force 'fork' method for this test since we use local functions.
    original_start_method = multiprocessing.get_start_method(allow_none=True)
    multiprocessing.set_start_method("fork", force=True)

    def child_eval_work(queue):
        """Child process with IAST disabled."""
        try:
            from ddtrace.appsec._iast._taint_tracking import is_tainted
            from ddtrace.internal.settings.asm import config as asm_config

            # IAST should be disabled, so this is a no-op
            _start_iast_context_and_oce()

            # Test eval - taint_pyobject is a no-op since IAST is disabled
            code = "1 + 1"
            tainted_code = taint_pyobject(code, "code_source", code, OriginType.PARAMETER)

            # Code should not be tainted (IAST disabled)
            is_obj_tainted = is_tainted(tainted_code)

            # This should not crash
            result = eval(tainted_code)

            is_enabled = asm_config._iast_enabled

            queue.put(("success", result, is_enabled, is_obj_tainted))
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
    assert result[2] is False, "IAST should be disabled in child"
    assert result[3] is False, "Code should not be tainted (IAST disabled)"

    # Restore original start method
    if original_start_method:
        multiprocessing.set_start_method(original_start_method, force=True)


def test_early_fork_keeps_iast_enabled():
    """
    Test that early forks (web workers) keep IAST enabled.

    This simulates the behavior of web framework workers like gunicorn/uvicorn
    that fork BEFORE IAST has any active context. In this case, IAST should
    remain enabled in the child and work normally.
    """
    from ddtrace.appsec._iast import _disable_iast_after_fork
    from ddtrace.appsec._iast._taint_tracking import initialize_native_state
    from ddtrace.appsec._iast._taint_tracking import is_tainted
    from ddtrace.internal.settings.asm import config as asm_config

    # Ensure IAST is enabled but NO context is active (simulating early fork)
    # Don't call _start_iast_context_and_oce() - this simulates pre-fork state
    initialize_native_state()
    original_state = asm_config._iast_enabled
    asm_config._iast_enabled = True

    # Call the fork handler - should detect no active context and keep IAST enabled
    _disable_iast_after_fork()

    # IAST should still be enabled (early fork scenario)
    assert asm_config._iast_enabled is True, "IAST should remain enabled for early forks"

    # Now we can initialize IAST fresh in this "worker"
    _start_iast_context_and_oce()

    # IAST should work normally
    tainted = taint_pyobject("worker_data", "source", "value", OriginType.PARAMETER)
    assert is_tainted(tainted), "IAST should work in early fork (web worker)"

    count = _num_objects_tainted_in_request()
    assert count > 0, "Should have tainted objects in early fork"

    _end_iast_context_and_oce()
    asm_config._iast_enabled = original_state


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
