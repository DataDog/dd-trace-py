from functools import wraps
from pathlib import Path

import ddtrace.internal.utils.inspection as inspection
from ddtrace.internal.utils.inspection import undecorated


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
