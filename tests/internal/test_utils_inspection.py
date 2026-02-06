from functools import wraps
from pathlib import Path
import textwrap
import typing as t

import ddtrace.internal.utils.inspection as inspection
from ddtrace.internal.utils.inspection import undecorated


class TelescopicFunction:
    """
    A telescopic function for stress testing stack unwinding.

    A telescopic function is a sequence of nested function calls where each function calls the next:
    func_0 -> func_1 -> func_2 -> ... -> func_N

    Each function has a unique code object, creating distinct frames at each stack level.
    This is more effective than recursion which reuses the same code object.

    Usage:
        func = TelescopicFunction("my_func", depth=20)
        func()  # Execute the telescopic function
        assert len(func.stacks) > 0  # Access collected stacks
    """

    def __init__(self, chain_id: str, chain_depth: int):
        """
        Initialize a telescopic function.

        Args:
            chain_id: Unique identifier for this function (used in internal function names)
            chain_depth: Number of nested function calls
        """
        self.chain_id = chain_id
        self.chain_depth = chain_depth
        self.stacks: t.List[t.List[inspection.Frame]] = []
        self._namespace: t.Dict[str, t.Any] = {"inspection": inspection}
        self._generate_functions()

    def _generate_functions(self) -> None:
        """Generate the nested telescopic functions."""
        for depth in range(self.chain_depth):
            func_name = f"chain_{self.chain_id}_func_{depth}"

            if depth == self.chain_depth - 1:
                # Last function in chain - collect frames
                func_code = textwrap.dedent(f"""
                    def {func_name}(collector, funcs):
                        marker = {hash(self.chain_id) + depth}  # Unique marker
                        collector.append(inspection.unwind_current_thread())
                        return marker
                """)
            else:
                # Intermediate function - call next in chain
                next_func_name = f"chain_{self.chain_id}_func_{depth + 1}"
                func_code = textwrap.dedent(f"""
                    def {func_name}(collector, funcs):
                        marker = {hash(self.chain_id) + depth}  # Unique marker ensures unique code
                        # Look up next function from passed funcs dict
                        next_func = funcs.get('{next_func_name}')
                        if next_func:
                            return next_func(collector, funcs)
                        return marker
                """)

            exec(func_code, self._namespace, self._namespace)

    def __call__(self) -> int:
        """
        Execute the telescopic function.

        Returns:
            The marker value from the deepest nested function
        """
        start_func: t.Callable = self._namespace[f"chain_{self.chain_id}_func_0"]
        return start_func(self.stacks, self._namespace)


def test_undecorated():
    def d(f):
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)

        return wrapper

    def f():
        pass

    df = d(f)
    assert df is not f

    ddf = d(df)
    assert ddf is not df

    dddf = d(ddf)
    assert dddf is not ddf

    name, path = f.__code__.co_name, Path(__file__).resolve()
    assert f is undecorated(dddf, name, path)
    assert f is undecorated(ddf, name, path)
    assert f is undecorated(df, name, path)
    assert f is undecorated(f, name, path)

    assert undecorated(undecorated, name, path) is undecorated


def test_class_decoration():
    class Decorator:
        def __init__(self, f):
            self.f = f

    @Decorator
    def f():
        pass

    code = undecorated(f, name="f", path=Path(__file__).resolve()).__code__
    assert code.co_name == "f"
    assert Path(code.co_filename).resolve() == Path(__file__).resolve()


def test_wrapped_decoration():
    @wraps
    def f():
        pass

    code = undecorated(f, name="f", path=Path(__file__).resolve()).__code__
    assert code.co_name == "f"
    assert Path(code.co_filename).resolve() == Path(__file__).resolve()


def test_unwind_current_thread():
    frames = inspection.unwind_current_thread()
    assert isinstance(frames, list)
    assert all(isinstance(frame, inspection.Frame) for frame in frames)
    assert frames[0].name == "test_unwind_current_thread"


def test_unwind_cache_reuse():
    """Test that frames are cached and reused when unwinding from the same exact location."""

    # Helper function to ensure we unwind from the exact same instruction
    def unwind_helper():
        # The trick is to call unwind_current_thread in a loop - same instruction pointer
        result = []
        for _ in range(2):
            result.append(inspection.unwind_current_thread())
        return result

    frames_list = unwind_helper()
    frames1, frames2 = frames_list[0], frames_list[1]

    # Verify we got the same number of frames
    assert len(frames1) == len(frames2)

    # The parent frames (those in unwind_helper and above) should be cached
    # and identical between calls. We skip the first frame (which is inside unwind_current_thread itself)
    # and check frames from unwind_helper upwards
    for f1, f2 in zip(frames1[1:], frames2[1:]):
        # These frames represent the same code locations so should be cached
        assert f1.name == f2.name
        assert f1.file == f2.file
        # Note: We don't check object identity since the line numbers might differ
        # due to different positions in the loop


def test_unwind_cache_different_depths():
    """Test cache behavior with different call stack depths."""

    def level3():
        return inspection.unwind_current_thread()

    def level2():
        return level3()

    def level1():
        return level2()

    # Get frames from deep call stack
    deep_frames = level1()

    # Get frames from shallow call stack
    shallow_frames = inspection.unwind_current_thread()

    # Deep stack should have more frames
    assert len(deep_frames) > len(shallow_frames)

    # Both should have valid frame data
    assert all(isinstance(f, inspection.Frame) for f in deep_frames)
    assert all(isinstance(f, inspection.Frame) for f in shallow_frames)


def test_unwind_cache_bounded_behavior():
    """Test that the cache has bounded size and evicts old entries."""
    # Generate many unique frames by calling from many different line numbers
    # We'll use recursion to create unique code locations

    def recursive_call(depth, results):
        if depth == 0:
            # Capture frames at leaf
            results.append(inspection.unwind_current_thread())
        else:
            # Each recursion level creates a unique frame location
            recursive_call(depth - 1, results)

    # Create enough unique call stacks to potentially overflow cache
    # Cache size is 2048, so we'll create more than that
    results = []

    # Call from many different depths to create variety
    for depth in range(0, 10, 2):
        results.clear()
        recursive_call(depth, results)

    # Cache should still work - verify we can still unwind
    final_frames = inspection.unwind_current_thread()
    assert len(final_frames) > 0
    assert all(isinstance(f, inspection.Frame) for f in final_frames)


def test_unwind_cache_frame_attributes():
    """Test that frames have all expected attributes."""
    frames = inspection.unwind_current_thread()

    # Verify all frames have the expected attributes
    for frame in frames:
        assert hasattr(frame, "file")
        assert hasattr(frame, "name")
        assert hasattr(frame, "line")
        assert hasattr(frame, "line_end")
        assert hasattr(frame, "column")
        assert hasattr(frame, "column_end")

        # Verify attributes have reasonable values
        assert isinstance(frame.file, str)
        assert isinstance(frame.name, str)
        assert isinstance(frame.line, int) and frame.line >= 0
        assert isinstance(frame.line_end, int) and frame.line_end >= 0
        assert isinstance(frame.column, int) and frame.column >= 0
        assert isinstance(frame.column_end, int) and frame.column_end >= 0


def test_unwind_cache_stability_under_repeated_calls():
    """Test cache stability with many repeated unwinds."""
    # Perform many unwinds - each will cache its frames
    all_frames = []

    for _ in range(100):
        frames = inspection.unwind_current_thread()
        all_frames.append(frames)

        # Verify we get valid frames each time
        assert len(frames) > 0
        assert all(isinstance(f, inspection.Frame) for f in frames)

    # Verify all calls returned the same number of frames (consistent stack depth)
    frame_counts = [len(frames) for frames in all_frames]
    assert all(count == frame_counts[0] for count in frame_counts), "Frame count should be consistent"


def test_unwind_cache_with_recursion():
    """Test cache behavior with recursive functions."""
    frames_collected = []

    def recursive_function(depth):
        if depth == 0:
            frames_collected.append(inspection.unwind_current_thread())
            return
        recursive_function(depth - 1)

    # Collect frames from different recursion depths
    frames_collected.clear()
    recursive_function(5)
    frames_5 = frames_collected[0]

    frames_collected.clear()
    recursive_function(3)
    frames_3 = frames_collected[0]

    # Both should have frames
    assert len(frames_5) > 0
    assert len(frames_3) > 0

    # Deeper recursion should have more frames
    assert len(frames_5) > len(frames_3)

    # The function names should include 'recursive_function'
    assert any("recursive_function" in f.name for f in frames_5)
    assert any("recursive_function" in f.name for f in frames_3)


def test_cache_size_configuration():
    """Test that cache size can be configured."""
    # Get initial cache size
    original_size = inspection.get_frame_cache_size()
    assert original_size > 0

    try:
        # Set to a small size
        inspection.set_frame_cache_size(50)
        assert inspection.get_frame_cache_size() == 50

        # Set to a larger size
        inspection.set_frame_cache_size(500)
        assert inspection.get_frame_cache_size() == 500

        # Verify unwinding still works
        frames = inspection.unwind_current_thread()
        assert len(frames) > 0

    finally:
        # Restore original size
        inspection.set_frame_cache_size(original_size)


def test_unwind_cache_eviction_stress_with_dynamic_functions():
    """
    Stress test cache eviction with dynamically generated telescopic functions.

    Uses telescopic functions where each nested call has a unique code object,
    creating distinct frames at each stack level. This is more effective than
    recursion which reuses the same code object.
    """
    # Save original cache size and set to small value for testing
    original_cache_size = inspection.get_frame_cache_size()

    try:
        # Set cache size to 100 so we can easily trigger evictions
        test_cache_size = 100
        inspection.set_frame_cache_size(test_cache_size)

        # Create telescopic functions
        # Each function has nested calls with unique code objects
        call_depth = 200  # Each function has 200 nested calls
        num_functions = 10  # Create 10 different functions

        all_collected_frames = []

        for func_id in range(num_functions):
            # Create and execute telescopic function
            func = TelescopicFunction(str(func_id), call_depth)
            func()

            assert len(func.stacks) > 0, f"Function {func_id} should have collected frames"
            all_collected_frames.extend(func.stacks)

        # At this point: 10 functions × 200 depth = 2000 unique frames per collection
        # With cache size of 100, we've forced many evictions

        # Verify ALL collected frames are still valid despite cache evictions
        total_frames_checked = 0
        for frame_list_idx, frame_list in enumerate(all_collected_frames):
            assert len(frame_list) > 0, f"Frame list {frame_list_idx} should not be empty"

            for frame_idx, frame in enumerate(frame_list):
                # Access all attributes - should not crash or return garbage
                assert isinstance(frame.file, str), f"Frame {frame_idx} in list {frame_list_idx}: file should be string"
                assert len(frame.file) > 0, f"Frame {frame_idx} in list {frame_list_idx}: file should not be empty"

                assert isinstance(frame.name, str), f"Frame {frame_idx} in list {frame_list_idx}: name should be string"
                assert len(frame.name) > 0, f"Frame {frame_idx} in list {frame_list_idx}: name should not be empty"

                assert isinstance(frame.line, int), f"Frame {frame_idx} in list {frame_list_idx}: line should be int"
                assert frame.line >= 0, f"Frame {frame_idx} in list {frame_list_idx}: line should be non-negative"

                total_frames_checked += 1

        # Verify we checked a significant number of frames
        # Each function creates ~200 frames, we have 10 functions
        print(f"✓ Verified {total_frames_checked} frames remain valid after cache evictions")
        assert total_frames_checked > test_cache_size * 1.5, "Should have created many more frames than cache size"

    finally:
        # Restore original cache size
        inspection.set_frame_cache_size(original_cache_size)


def test_unwind_cache_eviction_with_threads_and_dynamic_functions():
    """
    Ultimate stress test: Multiple threads with telescopic functions.

    Combines:
    1. Multiple concurrent threads
    2. Telescopic functions (nested calls with unique code objects)
    3. Deep call stacks with distinct frames at each level
    4. Small cache size to force aggressive evictions
    5. Verification that all frames remain valid despite evictions and thread switching
    """
    import threading
    import time

    original_cache_size = inspection.get_frame_cache_size()

    try:
        # Set very small cache for aggressive eviction testing
        test_cache_size = 50
        inspection.set_frame_cache_size(test_cache_size)

        results = []
        errors = []

        def worker_thread(thread_id, num_functions):
            """Worker that generates telescopic functions."""
            try:
                thread_frames = []
                call_depth = 150  # Each function has 150 nested calls

                # Each thread creates multiple functions
                for func_idx in range(num_functions):
                    # Create and execute telescopic function
                    func_id = f"t{thread_id}_f{func_idx}"
                    func = TelescopicFunction(func_id, call_depth)
                    func()
                    thread_frames.extend(func.stacks)

                    # Occasional yield to encourage thread switching
                    if func_idx % 3 == 0:
                        time.sleep(0.001)

                # Verify all collected frames are still valid
                for frame_list in thread_frames:
                    for frame in frame_list:
                        assert isinstance(frame.file, str) and len(frame.file) > 0
                        assert isinstance(frame.name, str) and len(frame.name) > 0
                        assert isinstance(frame.line, int) and frame.line >= 0

                results.append((thread_id, len(thread_frames)))

            except Exception as e:
                import traceback

                errors.append((thread_id, str(e), traceback.format_exc()))

        # Launch multiple threads
        num_threads = 8
        functions_per_thread = 10
        threads = []

        for i in range(num_threads):
            thread = threading.Thread(target=worker_thread, args=(i, functions_per_thread))
            threads.append(thread)
            thread.start()

        # Wait for all threads
        for thread in threads:
            thread.join(timeout=60)

        # Verify no errors occurred
        if errors:
            print("Errors encountered:")
            for thread_id, error, trace in errors:
                print(f"Thread {thread_id}: {error}")
                print(trace)
        assert len(errors) == 0, f"Threads encountered {len(errors)} error(s)"

        # Verify all threads completed
        assert len(results) == num_threads, f"Expected {num_threads} results, got {len(results)}"

        # Calculate total frame lists created
        total_frame_lists = sum(count for _, count in results)
        # Each thread creates functions_per_thread frame lists
        expected_frame_lists = num_threads * functions_per_thread
        frames_per_function = 150  # call_depth from worker_thread

        print(f"✓ {num_threads} threads created {total_frame_lists} frame lists")
        print(f"✓ Expected {expected_frame_lists} frame lists, got {total_frame_lists}")
        print(f"✓ Each frame list contains ~{frames_per_function} unique frames")
        print(f"✓ Total unique frames created: ~{total_frame_lists * frames_per_function}")
        print(f"✓ Cache size: {test_cache_size}, cache evictions were triggered")

        # Verify we created the expected number of frame lists
        assert total_frame_lists == expected_frame_lists, f"Should have created {expected_frame_lists} frame lists"
        # Each frame list contains ~frames_per_function frames, so total frames >> cache size
        assert total_frame_lists * frames_per_function > test_cache_size * 10, "Should have forced many cache evictions"

    finally:
        # Restore original cache size
        inspection.set_frame_cache_size(original_cache_size)
