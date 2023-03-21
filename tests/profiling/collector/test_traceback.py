import sys

from ddtrace.profiling.collector import _traceback


def _x():
    raise ValueError("hey!")


def test_check_traceback_to_frames():
    try:
        _x()
    except Exception:
        exc_type, exc_value, traceback = sys.exc_info()
    frames, nframes = _traceback.traceback_to_frames(traceback, 10)
    assert nframes == 2

    this_file = __file__.replace(".pyc", ".py")
    assert frames == [
        (this_file, 7, "_x", ""),
        (this_file, 15, "test_check_traceback_to_frames", ""),
    ]
