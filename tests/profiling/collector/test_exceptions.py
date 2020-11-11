import sys

import mock

from ddtrace.profiling import recorder
from ddtrace.profiling.collector import exceptions


def test_collect():
    r = recorder.Recorder()
    c = exceptions.UncaughtExceptionCollector(r)
    c.start()
    c.stop()


def _check_event(event):
    assert event.thread_name == "MainThread"
    assert isinstance(event.thread_id, int)
    assert event.frames == []
    assert event.exc_type == ValueError


def test_no_override():
    r = recorder.Recorder()
    c = exceptions.UncaughtExceptionCollector(r)
    c.start()
    sys.excepthook(ValueError, ValueError(), None)
    c.stop()
    sys.excepthook(ValueError, ValueError(), None)
    events = r.events[exceptions.UncaughtExceptionEvent]
    assert len(events) == 1
    _check_event(events[0])


def test_with_override():
    seen = {"seen": False}

    def myhook(exctype, value, traceback):
        seen["seen"] = True

    sys.excepthook = myhook
    r = recorder.Recorder()
    c = exceptions.UncaughtExceptionCollector(r)
    c.start()
    sys.excepthook(ValueError, ValueError(), None)
    c.stop()
    assert seen["seen"]
    seen["seen"] = False
    sys.excepthook(ValueError, ValueError(), None)
    assert seen["seen"]
    events = r.events[exceptions.UncaughtExceptionEvent]
    assert len(events) == 1
    _check_event(events[0])


def test_call_original_on_failure():
    r = mock.Mock()

    def _raise(self):
        raise RuntimeError("oops")

    r.push_event.side_effect = _raise

    c = exceptions.UncaughtExceptionCollector(r)
    called = {"called": False}

    def set_called(exctype, value, tb):
        called["called"] = True

    orig_excepthook = sys.excepthook
    sys.excepthook = set_called

    try:
        c.start()
        c.except_hook(None, None, None)
        c.stop()
        assert called["called"]
    finally:
        sys.excepthook = orig_excepthook
