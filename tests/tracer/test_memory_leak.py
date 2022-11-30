"""
Variety of test cases ensuring that ddtrace does not leak memory.
"""
import gc
import os
from threading import Thread
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

import pytest

from ddtrace import Tracer


if TYPE_CHECKING:  # pragma: no cover
    from ddtrace.span import Span


@pytest.fixture
def tracer():
    # type: (...) -> Tracer
    return Tracer()


def trace(weakdict, tracer, *args, **kwargs):
    # type: (WeakValueDictionary, Tracer, ...) -> Span
    """Return a span created from ``tracer`` and add it to the given weak
    dictionary.

    Note: ensure to delete the returned reference from this function to ensure
    no additional references are kept to the span.
    """
    s = tracer.trace(*args, **kwargs)
    weakdict[s.span_id] = s
    return s


def test_leak(tracer):
    wd = WeakValueDictionary()
    span = trace(wd, tracer, "span1")
    span2 = trace(wd, tracer, "span2")
    assert len(wd) == 2

    # The spans are still open and referenced so they should not be gc'd
    gc.collect()
    assert len(wd) == 2
    span2.finish()
    span.finish()
    del span, span2
    gc.collect()
    assert len(wd) == 0


def test_single_thread_single_trace(tracer):
    """
    Ensure a simple trace doesn't leak span objects.
    """
    wd = WeakValueDictionary()
    with trace(wd, tracer, "span1"):
        with trace(wd, tracer, "span2"):
            pass

    # Spans are serialized and unreferenced when traces are finished
    # so gc-ing right away should delete all span objects.
    gc.collect()
    assert len(wd) == 0


def test_single_thread_multi_trace(tracer):
    """
    Ensure a trace in a thread is properly garbage collected.
    """
    wd = WeakValueDictionary()
    for _ in range(1000):
        with trace(wd, tracer, "span1"):
            with trace(wd, tracer, "span2"):
                pass
            with trace(wd, tracer, "span3"):
                pass

    # Once these references are deleted then the spans should no longer be
    # referenced by anything and should be gc'd.
    gc.collect()
    assert len(wd) == 0


def test_multithread_trace(tracer):
    """
    Ensure a trace that crosses thread boundaries is properly garbage collected.
    """
    wd = WeakValueDictionary()
    state = []

    def _target(ctx):
        tracer.context_provider.activate(ctx)
        with trace(wd, tracer, "thread"):
            assert len(wd) == 2
        state.append(1)

    span = trace(wd, tracer, "")
    t = Thread(target=_target, args=(span.context,))
    t.start()
    t.join()
    # Ensure thread finished successfully
    assert state == [1]

    span.finish()
    del span
    gc.collect()
    assert len(wd) == 0


def test_fork_open_span(tracer):
    """
    When a fork occurs with an open span then the child process should not have
    a strong reference to the span because it might never be closed.
    """
    wd = WeakValueDictionary()
    span = trace(wd, tracer, "span")
    pid = os.fork()

    if pid == 0:
        assert len(wd) == 1
        gc.collect()
        # span is still open and in the context
        assert len(wd) == 1
        span2 = trace(wd, tracer, "span2")
        assert span2._parent is None
        assert len(wd) == 2
        span2.finish()

        del span2
        # Expect there to be one span left (the original from before the fork)
        # which is inherited into the child process but will never be closed.
        # The important thing in this test case is all new spans created in the
        # child will be gc'd.
        gc.collect()
        assert len(wd) == 1

        # Normally, if the child process leaves this function frame the span
        # reference would be lost and it would be free to be gc'd. We delete
        # the reference explicitly here to mimic this scenario.
        del span
        gc.collect()
        assert len(wd) == 0
        os._exit(12)

    assert len(wd) == 1
    span.finish()
    del span
    gc.collect()
    assert len(wd) == 0

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12
