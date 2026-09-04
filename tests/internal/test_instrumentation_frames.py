import sys

import pytest

from ddtrace.internal import _instrumentation_frames as frames


@pytest.fixture(autouse=True)
def clean_registry():
    saved = set(frames._passthrough_codes)
    frames._passthrough_codes.clear()
    try:
        yield
    finally:
        frames._passthrough_codes.clear()
        frames._passthrough_codes.update(saved)


def _traceback_of(fn):
    try:
        fn()
    except BaseException as exc:
        return exc.__traceback__
    raise AssertionError("expected the call to raise")


def _names(summaries):
    return [summary.name for summary in summaries]


def wrapper_forwarding(original, *args, **kwargs):
    """Stands in for an appsec wrapt wrapper: does its own work, then forwards."""
    return original(*args, **kwargs)


def wrapper_raising_explicitly(original, *args, **kwargs):
    raise ValueError("a bug in the wrapper itself")


def wrapper_raising_implicitly(original, *args, **kwargs):
    empty = {}
    return empty["missing"]


def target_python_function():
    raise RuntimeError("the application's own error")


def test_an_unregistered_wrapper_frame_is_reported():
    tb = _traceback_of(lambda: wrapper_forwarding(target_python_function))

    assert "wrapper_forwarding" in _names(frames.extract_reportable_frames(tb))


def test_a_registered_wrapper_frame_is_dropped_when_a_python_callee_raised():
    frames.mark_passthrough(wrapper_forwarding)
    tb = _traceback_of(lambda: wrapper_forwarding(target_python_function))

    reported = _names(frames.extract_reportable_frames(tb))
    assert "wrapper_forwarding" not in reported
    assert "target_python_function" in reported


def test_a_registered_wrapper_frame_is_dropped_when_a_c_callee_raised():
    """The C callee owns no frame, so the wrapper is the deepest frame and looks like the raiser."""
    frames.mark_passthrough(wrapper_forwarding)
    tb = _traceback_of(lambda: wrapper_forwarding(open, "/nonexistent/definitely-not-here"))

    assert "wrapper_forwarding" not in _names(frames.extract_reportable_frames(tb))


@pytest.mark.parametrize("wrapper", [wrapper_raising_explicitly, wrapper_raising_implicitly])
def test_a_registered_wrapper_keeps_its_frame_when_it_raised_itself(wrapper):
    """Genuine ddtrace faults must stay attributed to us, registered or not."""
    frames.mark_passthrough(wrapper)
    tb = _traceback_of(lambda: wrapper(target_python_function))

    assert wrapper.__name__ in _names(frames.extract_reportable_frames(tb))


def test_nothing_is_dropped_when_the_registry_is_empty():
    tb = _traceback_of(target_python_function)

    import traceback as stdlib_traceback

    assert frames.extract_reportable_frames(tb) == stdlib_traceback.extract_tb(tb)


def test_an_all_passthrough_traceback_is_reported_rather_than_emptied():
    frames.mark_passthrough(wrapper_forwarding)
    tb = _traceback_of(lambda: wrapper_forwarding(open, "/nonexistent/definitely-not-here"))
    # Drop the lambda and this module's helper too, so every remaining frame is a passthrough.
    while tb.tb_next is not None and tb.tb_frame.f_code is not wrapper_forwarding.__code__:
        tb = tb.tb_next

    assert _names(frames.extract_reportable_frames(tb)) == ["wrapper_forwarding"]


def test_a_truncated_traceback_is_reported_unfiltered():
    """extract_tb honours sys.tracebacklimit but the raw walk does not, so they stop lining up."""
    frames.mark_passthrough(wrapper_forwarding)
    tb = _traceback_of(lambda: wrapper_forwarding(target_python_function))

    sys.tracebacklimit = 1
    try:
        reported = frames.extract_reportable_frames(tb)
    finally:
        del sys.tracebacklimit

    assert len(reported) == 1
