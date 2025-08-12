# -*- coding: utf-8 -*-
from multiprocessing import Process
from multiprocessing import Queue
import os
import sys
import time
import uuid

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import debug_taint_map
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking import num_objects_tainted
from ddtrace.appsec._iast._taint_tracking._context import create_context
from ddtrace.appsec._iast._taint_tracking._context import reset_context
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
def test_fork_taint_isolation():
    """Test that taint tracking state is properly isolated between parent and child processes."""

    def child_process_work(queue):
        """Work function for child process to validate taint isolation."""
        try:
            # Create context in child process
            create_context()

            # Verify child starts with clean state
            initial_count = num_objects_tainted()
            queue.put(("child_initial_count", initial_count))

            # Create tainted objects in child
            child_tainted = taint_pyobject(
                "child_data", source_name="child_source", source_value="child_value", source_origin=OriginType.PARAMETER
            )

            child_count = num_objects_tainted()
            queue.put(("child_tainted_count", child_count))

            # Verify child can access its own tainted data
            child_ranges = get_ranges(child_tainted)
            queue.put(("child_ranges_exist", len(child_ranges) > 0))

            # Create more complex taint operations
            child_str2 = taint_pyobject(
                "child_data2", source_name="child_source2", source_value="child_value2", source_origin=OriginType.COOKIE
            )

            # Test taint propagation in child
            child_combined = add_aspect(child_tainted, child_str2)
            combined_ranges = get_ranges(child_combined)
            queue.put(("child_combined_ranges", len(combined_ranges)))

            final_count = num_objects_tainted()
            queue.put(("child_final_count", final_count))

            # Get debug info from child
            child_debug_map = debug_taint_map()
            queue.put(("child_debug_map_empty", child_debug_map == "[]"))

        except Exception as e:
            queue.put(("child_error", str(e)))

    # Parent process setup
    create_context()

    # Create tainted objects in parent before fork
    parent_tainted = taint_pyobject(
        "parent_data", source_name="parent_source", source_value="parent_value", source_origin=OriginType.HEADER_NAME
    )

    parent_initial_count = num_objects_tainted()
    assert parent_initial_count == 1

    # Verify parent can access its tainted data
    parent_ranges = get_ranges(parent_tainted)
    assert len(parent_ranges) > 0

    # Fork the process
    queue = Queue()
    child_process = Process(target=child_process_work, args=(queue,))
    child_process.start()
    child_process.join()

    # Collect results from child
    child_results = {}
    while not queue.empty():
        key, value = queue.get()
        child_results[key] = value

    # Verify child process results
    assert "child_error" not in child_results, f"Child process error: {child_results.get('child_error')}"

    # Child should start with clean state (thread-local storage should be fresh)
    assert child_results["child_initial_count"] == 0, "Child should start with no tainted objects"

    # Child should be able to create its own tainted objects
    assert child_results["child_tainted_count"] == 1, "Child should have 1 tainted object after creation"
    assert child_results["child_ranges_exist"] is True, "Child should be able to access its tainted ranges"

    # Child should be able to perform taint operations
    assert child_results["child_combined_ranges"] >= 2, "Child should have combined ranges from both sources"
    assert child_results["child_final_count"] >= 2, "Child should have multiple tainted objects"

    # Verify parent state is unchanged after child execution
    parent_final_count = num_objects_tainted()
    assert parent_final_count == parent_initial_count, "Parent taint count should be unchanged"

    # Parent should still be able to access its original tainted data
    parent_ranges_after = get_ranges(parent_tainted)
    assert len(parent_ranges_after) > 0, "Parent should still have access to its tainted data"
    assert parent_ranges_after == parent_ranges, "Parent ranges should be unchanged"


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
def test_fork_multiple_children():
    """Test taint isolation with multiple child processes."""

    def child_worker(child_id, queue):
        """Worker function for each child process."""
        try:
            create_context()

            # Each child creates unique tainted data
            child_data = f"child_{child_id}_data"
            tainted_obj = taint_pyobject(
                child_data,
                source_name=f"child_{child_id}_source",
                source_value=f"child_{child_id}_value",
                source_origin=OriginType.PARAMETER,
            )

            # Verify isolation
            count = num_objects_tainted()
            ranges = get_ranges(tainted_obj)

            queue.put((child_id, count, len(ranges) > 0))

        except Exception as e:
            queue.put((child_id, "error", str(e)))

    # Parent setup
    create_context()
    parent_tainted = taint_pyobject(  # noqa: F841
        "parent_shared_data", source_name="parent_source", source_value="parent_value", source_origin=OriginType.BODY
    )

    parent_count = num_objects_tainted()
    assert parent_count == 1

    # Create multiple child processes
    num_children = 3
    queue = Queue()
    children = []

    for i in range(num_children):
        child = Process(target=child_worker, args=(i, queue))
        children.append(child)
        child.start()

    # Wait for all children
    for child in children:
        child.join()

    # Collect results
    child_results = {}
    while not queue.empty():
        result = queue.get()
        if len(result) == 3:
            child_id, count, has_ranges = result
            child_results[child_id] = {"count": count, "has_ranges": has_ranges}
        else:
            child_id, error_type, error_msg = result
            child_results[child_id] = {"error": f"{error_type}: {error_msg}"}

    # Verify all children worked independently
    for i in range(num_children):
        assert i in child_results, f"Child {i} should have reported results"
        assert "error" not in child_results[i], f"Child {i} should not have errors: {child_results[i]}"
        assert child_results[i]["count"] == 1, f"Child {i} should have exactly 1 tainted object"
        assert child_results[i]["has_ranges"] is True, f"Child {i} should have taint ranges"

    # Verify parent is unchanged
    final_parent_count = num_objects_tainted()
    assert final_parent_count == parent_count, "Parent taint count should remain unchanged"


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
def test_fork_with_os_fork():
    """Test fork safety using os.fork() directly."""

    # Parent setup
    create_context()
    parent_data = taint_pyobject(
        "parent_before_fork", source_name="parent_source", source_value="parent_value", source_origin=OriginType.PATH
    )

    parent_count_before = num_objects_tainted()
    assert parent_count_before == 1

    # Fork using os.fork()
    pid = os.fork()

    if pid == 0:
        # Child process
        try:
            # Child should have clean thread-local state
            child_count_initial = num_objects_tainted()
            # Verify child isolation 1
            assert child_count_initial == 1, f"Child should start with 0 tainted objects, got {child_count_initial}"
            # Create new context in child (should be isolated)
            create_context()

            # Create child-specific tainted data
            num_objects = 3
            for _ in range(num_objects):
                data = f"child_after_fork_{uuid.uuid4()}"
                print(data)
                child_data = taint_pyobject(
                    data, source_name="child_source", source_value=data, source_origin=OriginType.COOKIE
                )

            child_count_after = num_objects_tainted()

            # Verify child isolation 2
            assert (
                child_count_after == num_objects
            ), f"Child should have 1 tainted object after creation, got {child_count_after}"

            # Verify child can access its own data
            child_ranges = get_ranges(child_data)
            assert len(child_ranges) > 0, "Child should be able to access its tainted ranges"

            # Test that child cannot access parent's pre-fork data

            parent_ranges_in_child = get_ranges(parent_data)
            # If we can get ranges, they should be empty due to isolation
            assert len(parent_ranges_in_child) == 0, "Child should not have access to parent's pre-fork taint data"

            # Child exits successfully
            os._exit(0)

        except Exception as e:
            print(f"Child process error: {e}", file=sys.stderr)
            os._exit(1)

    else:
        # Parent process
        # Wait for child to complete
        _, status = os.waitpid(pid, 0)
        assert status == 0, "Child process should exit successfully"

        # Verify parent state is preserved
        parent_count_after = num_objects_tainted()
        assert parent_count_after == parent_count_before, "Parent taint count should be unchanged"

        # Verify parent can still access its data
        parent_ranges = get_ranges(parent_data)
        assert len(parent_ranges) > 0, "Parent should still have access to its tainted data"


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
def test_fork_context_reset_isolation():
    """Test that context resets in parent don't affect child processes."""

    def child_with_context_operations(queue):
        """Child process that performs various context operations."""
        try:
            create_context()

            # Create initial tainted data
            data1 = taint_pyobject("child_data1", "source1", "value1", OriginType.PARAMETER)  # noqa: F841
            count1 = num_objects_tainted()

            # Reset and recreate context
            reset_context()
            create_context()

            # Should start fresh
            count_after_reset = num_objects_tainted()

            # Create new tainted data
            data2 = taint_pyobject("child_data2", "source2", "value2", OriginType.HEADER_NAME)  # noqa: F841
            count2 = num_objects_tainted()

            queue.put({"initial_count": count1, "count_after_reset": count_after_reset, "final_count": count2})

        except Exception as e:
            queue.put({"error": str(e)})

    # Parent setup with context
    create_context()
    _ = taint_pyobject("parent_data", "parent_source", "parent_value", OriginType.BODY)

    # Start child process
    queue = Queue()
    child = Process(target=child_with_context_operations, args=(queue,))
    child.start()

    # While child is running, perform parent operations
    time.sleep(0.1)  # Give child time to start

    # Reset parent context
    reset_context()
    create_context()

    # Create new parent data
    _ = taint_pyobject("parent_data2", "parent_source2", "parent_value2", OriginType.COOKIE)
    parent_final_count = num_objects_tainted()

    # Wait for child to complete
    child.join()

    # Get child results
    child_result = queue.get()
    assert "error" not in child_result, f"Child error: {child_result.get('error')}"

    # Verify child operated independently
    assert child_result["initial_count"] == 1, "Child should have had 1 tainted object initially"
    assert child_result["count_after_reset"] == 0, "Child should have 0 objects after reset"
    assert child_result["final_count"] == 1, "Child should have 1 object after recreating context"

    # Verify parent operations were independent
    assert parent_final_count == 1, "Parent should have 1 tainted object after reset and recreation"


if __name__ == "__main__":
    # Run a simple test when executed directly
    test_fork_taint_isolation()
    print("Fork taint isolation test passed!")
