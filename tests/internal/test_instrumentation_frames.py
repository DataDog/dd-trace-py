import functools
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
    """The C callee owns no frame, so the wrapper is the deepest frame and looks like the raiser.

    int, not open: AppSec patches builtins.open, which would make this depend on whether anything
    else in the session enabled ASM.
    """
    frames.mark_passthrough(wrapper_forwarding)
    tb = _traceback_of(lambda: wrapper_forwarding(int, "not a number"))

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
    tb = _traceback_of(lambda: wrapper_forwarding(int, "not a number"))
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


def wrapper_via_partial(original, *args, **kwargs):
    return original(*args, **kwargs)


def test_a_partial_wrapper_can_be_registered():
    """functools.partial has no __code__, so registering one used to be a silent no-op.

    IAST installs its security-control wrappers as partials, so this is a real shape.
    """
    partial_wrapper = functools.partial(wrapper_via_partial)
    frames.mark_passthrough(partial_wrapper)

    assert wrapper_via_partial.__code__ in frames._passthrough_codes

    tb = _traceback_of(lambda: partial_wrapper(int, "not a number"))
    assert "wrapper_via_partial" not in _names(frames.extract_reportable_frames(tb))


def delegating_wrapper(original, *args, **kwargs):
    return forwarding_delegate(original, *args, **kwargs)


def forwarding_delegate(original, *args, **kwargs):
    return original(*args, **kwargs)


def test_registering_a_hook_does_not_cover_a_delegate_it_forwards_through():
    """Only the registered frame is dropped, so a shared delegate has to be registered too.

    This is the shape of the IAST weak-hash sinks: the installed hook delegates the actual call.
    """
    frames.mark_passthrough(delegating_wrapper)
    tb = _traceback_of(lambda: delegating_wrapper(int, "not a number"))
    reported = _names(frames.extract_reportable_frames(tb))
    assert "delegating_wrapper" not in reported
    assert "forwarding_delegate" in reported, "the delegate is not covered by the hook"

    frames.mark_passthrough(forwarding_delegate)
    tb = _traceback_of(lambda: delegating_wrapper(int, "not a number"))
    assert "forwarding_delegate" not in _names(frames.extract_reportable_frames(tb))


def wrapper_that_fails_on_its_own(original, *args, **kwargs):
    import json

    json.loads("{ not json")
    return original(*args, **kwargs)


def test_a_registered_wrapper_that_fails_through_a_python_callee():
    """Known limitation, pinned deliberately rather than fixed.

    A registered wrapper that is not the deepest frame is always dropped, because a traceback does
    not record which callee was the wrapped one. Here our own json call raised, so the frame is
    dropped and the report blames the stdlib. The bias is intentional - the point of this filter
    is to stop blaming Datadog for application errors - and no registered wrapper today reaches
    this state: they either swallow their own exceptions or fail through ddtrace frames that stay.
    """
    frames.mark_passthrough(wrapper_that_fails_on_its_own)
    tb = _traceback_of(lambda: wrapper_that_fails_on_its_own(int, "1"))

    reported = _names(frames.extract_reportable_frames(tb))
    assert "wrapper_that_fails_on_its_own" not in reported
    assert "raw_decode" in reported
